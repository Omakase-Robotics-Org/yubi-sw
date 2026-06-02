"""Unit tests for ``airoa_quest_bridge.transport.tcp_json``.

Exercises the parts that don't need a live socket: the canonical
``QuestFrame`` shape, the ``_safe_float`` helper, the public ``metrics``
snapshot, and the recv-loop decode path (driven by a fake socket).
"""

from __future__ import annotations

import json
import math
import threading
import time
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def transport_module(mock_ros):
    """Import ``transport.tcp_json`` after the ROS mocks are in place."""
    import airoa_quest_bridge.transport.tcp_json as mod

    return mod


class _FakeSocket:
    """Minimal stand-in for a non-blocking TCP socket.

    ``feed`` queues bytes that the bridge's recv loop will read on its next
    ``recv()`` call. ``recv`` raises ``BlockingIOError`` when the queue is
    empty (mirroring a real non-blocking socket with no data).
    """

    def __init__(self):
        self._chunks: list = []
        self._closed = False
        self._lock = threading.Lock()

    def feed(self, data: bytes):
        with self._lock:
            self._chunks.append(data)

    def close_remote(self):
        """Schedule a recv that returns b'' (peer-closed semantics)."""
        with self._lock:
            self._chunks.append(b"")

    def recv(self, _size):
        with self._lock:
            if not self._chunks:
                raise BlockingIOError
            return self._chunks.pop(0)

    def shutdown(self, _how):
        self._closed = True

    def close(self):
        self._closed = True


# ---------------------------------------------------------------------------
# QuestFrame dataclass
# ---------------------------------------------------------------------------


def test_quest_frame_defaults_protocol_neutral_fields(transport_module):
    """``QuestFrame`` should accept the protocol-neutral schema."""
    f = transport_module.QuestFrame(
        device_time_ns=123,
        pc_monotonic_ns=456,
        seq=0,
        quest_id="",
        delta_time_s=float("nan"),
    )
    assert f.device_time_ns == 123
    assert f.pc_monotonic_ns == 456
    assert f.seq == 0
    assert f.quest_id == ""
    assert math.isnan(f.delta_time_s)
    assert f.raw == {}


# ---------------------------------------------------------------------------
# _safe_float helper
# ---------------------------------------------------------------------------


def test_safe_float_handles_missing_and_invalid(transport_module):
    sf = transport_module._safe_float
    assert sf(1.5) == 1.5
    assert sf("2.0") == 2.0
    assert sf(3) == 3.0
    assert math.isnan(sf(None))
    assert math.isnan(sf("not a number"))
    assert math.isnan(sf({"x": 1}))


# ---------------------------------------------------------------------------
# metrics() snapshot
# ---------------------------------------------------------------------------


def test_metrics_initial_state(transport_module):
    """Before any frame arrives, metrics should be in a defined idle state."""
    t = transport_module.TcpJsonTransport(
        ip="",  # disable real connect attempts
        tcp_port=0,
        sync_port=0,
        on_frame=lambda _f: None,
    )
    m = t.metrics()
    assert m["connected"] is False
    assert math.isnan(m["last_frame_age_s"])
    assert m["fps"] == 0.0
    assert m["offset_ns"] == 0
    assert m["rtt_ns"] is None


def test_try_connect_no_ip_is_noop(transport_module):
    """Empty IP should not even attempt to open a socket (legacy behavior)."""
    t = transport_module.TcpJsonTransport(
        ip="",
        tcp_port=0,
        sync_port=0,
        on_frame=lambda _f: None,
    )
    t.try_connect()
    assert t.connected is False


# ---------------------------------------------------------------------------
# Recv-loop frame decoding
#
# We bypass ``try_connect`` (which would open a real socket) and inject a
# ``_FakeSocket`` directly into the transport's ``_running``/``_tcp_sock``
# state, then run ``_tcp_recv_loop`` in a thread the same way the production
# code does. This exercises the JSON parsing and ``QuestFrame`` construction
# without requiring network or rclpy.
# ---------------------------------------------------------------------------


def _drive_recv_loop(transport, sock):
    """Set up minimal connected state and run the recv loop in a thread."""
    transport._tcp_sock = sock
    transport._running = True
    transport._connected = True
    th = threading.Thread(target=transport._tcp_recv_loop, daemon=True)
    th.start()
    return th


def _wait_for(predicate, timeout=2.0, poll=0.005):
    """Poll until ``predicate()`` is true or timeout elapses."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(poll)
    return False


def test_recv_loop_decodes_frame_and_invokes_callback(transport_module):
    """One newline-delimited JSON message produces one ``QuestFrame``."""
    received: list = []
    t = transport_module.TcpJsonTransport(
        ip="",
        tcp_port=0,
        sync_port=0,
        on_frame=received.append,
    )

    sock = _FakeSocket()
    th = _drive_recv_loop(t, sock)

    payload = {
        "ovrTimeNs": 1_000_000_000,
        "deltaTime": 0.02,
        "hmdPosition": {"x": 0.1, "y": 0.2, "z": 0.3},
    }
    sock.feed((json.dumps(payload) + "\n").encode())

    assert _wait_for(lambda: len(received) == 1), "QuestFrame was not produced"
    sock.close_remote()
    th.join(timeout=2.0)

    frame = received[0]
    assert frame.device_time_ns == 1_000_000_000
    assert frame.delta_time_s == pytest.approx(0.02)
    assert frame.raw["hmdPosition"]["x"] == pytest.approx(0.1)
    # Protocol-neutral defaults that the legacy TCP/JSON path can't fill.
    assert frame.seq == 0
    assert frame.quest_id == ""
    # pc_monotonic_ns is captured by the loop; it should be a positive int.
    assert isinstance(frame.pc_monotonic_ns, int)
    assert frame.pc_monotonic_ns > 0


def test_recv_loop_handles_chunked_and_multi_message_input(transport_module):
    """Frame boundaries can split mid-message; multiple per recv must work."""
    received: list = []
    t = transport_module.TcpJsonTransport(
        ip="",
        tcp_port=0,
        sync_port=0,
        on_frame=received.append,
    )

    sock = _FakeSocket()
    th = _drive_recv_loop(t, sock)

    msg_a = json.dumps({"ovrTimeNs": 1, "tag": "a"})
    msg_b = json.dumps({"ovrTimeNs": 2, "tag": "b"})
    msg_c = json.dumps({"ovrTimeNs": 3, "tag": "c"})

    # Split an outgoing buffer mid-message and bundle two messages in another
    # chunk to verify the line-buffering logic.
    combined = (msg_a + "\n" + msg_b + "\n" + msg_c + "\n").encode()
    sock.feed(combined[:20])
    sock.feed(combined[20:])

    assert _wait_for(lambda: len(received) == 3), (
        f"expected 3 frames, got {len(received)}"
    )
    sock.close_remote()
    th.join(timeout=2.0)

    tags = [f.raw["tag"] for f in received]
    assert tags == ["a", "b", "c"]


def test_recv_loop_skips_invalid_json_lines(transport_module):
    """Garbage lines must not break the stream; valid lines still decode."""
    received: list = []
    t = transport_module.TcpJsonTransport(
        ip="",
        tcp_port=0,
        sync_port=0,
        on_frame=received.append,
    )

    sock = _FakeSocket()
    th = _drive_recv_loop(t, sock)

    sock.feed(b"this is not json\n")
    sock.feed((json.dumps({"ovrTimeNs": 42}) + "\n").encode())

    assert _wait_for(lambda: len(received) == 1)
    sock.close_remote()
    th.join(timeout=2.0)

    assert received[0].device_time_ns == 42


def test_recv_loop_disconnects_on_zero_byte_recv(transport_module):
    """``sock.recv()`` returning empty bytes should mark the transport closed."""
    state_changes: list = []

    def _on_state(connected, reason):
        state_changes.append((connected, reason))

    t = transport_module.TcpJsonTransport(
        ip="",
        tcp_port=0,
        sync_port=0,
        on_frame=lambda _f: None,
        on_state_change=_on_state,
    )

    sock = _FakeSocket()
    th = _drive_recv_loop(t, sock)
    sock.close_remote()
    th.join(timeout=2.0)

    assert t.connected is False
    assert state_changes and state_changes[-1][0] is False


def test_callback_exception_does_not_crash_loop(transport_module):
    """A raising on_frame callback must be logged but not abort recv."""
    calls: list = []

    def _bad(_f):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")

    logger = MagicMock()
    t = transport_module.TcpJsonTransport(
        ip="",
        tcp_port=0,
        sync_port=0,
        on_frame=_bad,
        logger=logger,
    )
    sock = _FakeSocket()
    th = _drive_recv_loop(t, sock)

    sock.feed((json.dumps({"ovrTimeNs": 1}) + "\n").encode())
    sock.feed((json.dumps({"ovrTimeNs": 2}) + "\n").encode())

    assert _wait_for(lambda: len(calls) >= 2)
    sock.close_remote()
    th.join(timeout=2.0)

    # The loop logged the failure rather than dying.
    assert any(
        "on_frame callback raised" in str(c.args[0]) for c in logger.warn.call_args_list
    )
