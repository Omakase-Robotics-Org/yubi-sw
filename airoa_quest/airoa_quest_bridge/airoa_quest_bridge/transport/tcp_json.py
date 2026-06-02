"""TCP/JSON transport for the legacy in-house Quest streaming protocol.

This module owns all wire-protocol details for the current TCP-based JSON
stream and the companion UDP NTP-style time-sync handshake. The bridge node
sees only a normalized ``QuestFrame`` per sample plus a metrics snapshot for
diagnostics.

When the project migrates to ``yubi_quest_app`` (UDP/binary) a sibling module
``transport/udp_binary.py`` will be added with the same public surface so the
node can switch via a parameter without touching the publishing logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import socket
import statistics
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional


@dataclass
class QuestFrame:
    """Protocol-neutral per-sample snapshot handed to the bridge node.

    ``raw`` carries the underlying decoded payload so publishers that still
    need the legacy field shape (``/tf``, ``/quest/joy``, battery, etc.) can
    keep reading their existing keys without going through this dataclass.
    """

    device_time_ns: int  # 0 if the protocol does not provide one
    pc_monotonic_ns: int  # time.monotonic_ns() captured at receive
    seq: int  # 0 if the protocol does not provide one
    quest_id: str  # "" if the protocol does not provide one
    delta_time_s: float  # NaN if the protocol does not report it
    raw: Dict[str, Any] = field(default_factory=dict)


class TcpJsonTransport:
    """Manage the TCP/JSON stream and UDP time-sync side-channel.

    Threading model:
      * The owning node calls :meth:`try_connect` from a ROS timer to (re)open
        the TCP socket on the rclpy executor thread.
      * Once connected, two daemon threads run for the lifetime of the
        connection: one drains TCP frames, one performs periodic NTP-style
        offset estimation over UDP.
      * The ``on_frame`` callback is invoked from the TCP recv thread. rclpy
        publishers are thread-safe, so the node may publish directly from it.
    """

    def __init__(
        self,
        *,
        ip: str,
        tcp_port: int,
        sync_port: int,
        on_frame: Callable[[QuestFrame], None],
        on_state_change: Optional[Callable[[bool, str], None]] = None,
        logger: Any = None,
        rtt_accept_ns: int = 8_000_000,
        offset_history: int = 15,
        fps_window: int = 30,
    ) -> None:
        self._ip = ip
        self._tcp_port = int(tcp_port)
        self._sync_port = int(sync_port)
        self._on_frame = on_frame
        self._on_state_change = on_state_change
        self._log = logger
        self._rtt_accept_ns = int(rtt_accept_ns)
        self._offset_history = int(offset_history)
        self._fps_window = int(fps_window)

        self._lock = threading.Lock()
        self._tcp_sock: Optional[socket.socket] = None
        self._running = False
        self._connected = False

        # Metrics (read under _lock)
        self._frame_times: List[float] = []
        self._last_frame_mono: Optional[float] = None
        self._offset_ns: int = 0
        self._offset_hist: List[int] = []
        self._rtt_ns: Optional[int] = None

    # ---------- public API ----------

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._connected

    def metrics(self) -> Dict[str, Any]:
        """Return a snapshot of connection / timing metrics for diagnostics."""
        now_mono = time.monotonic()
        with self._lock:
            connected = self._connected
            last_frame_mono = self._last_frame_mono
            frame_times = tuple(self._frame_times)
            off_ns = self._offset_ns
            rtt_ns = self._rtt_ns

        if len(frame_times) >= 2 and frame_times[-1] > frame_times[0]:
            fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
        else:
            fps = 0.0

        last_frame_age_s = (
            float("nan") if last_frame_mono is None else now_mono - last_frame_mono
        )
        return {
            "connected": connected,
            "last_frame_age_s": last_frame_age_s,
            "fps": fps,
            "offset_ns": off_ns,
            "rtt_ns": rtt_ns,
        }

    def try_connect(self) -> None:
        """Attempt one TCP connect. No-op if already connected."""
        with self._lock:
            if self._connected:
                return

        if not self._ip:
            return

        sock: Optional[socket.socket] = None
        try:
            self._info(f"Connecting TCP {self._ip}:{self._tcp_port} ...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_QUICKACK, 1)
            except OSError:
                pass

            sock.settimeout(2.0)
            sock.connect((self._ip, self._tcp_port))
            sock.settimeout(None)
            sock.setblocking(False)

            with self._lock:
                self._tcp_sock = sock
                self._running = True
                self._connected = True
                self._frame_times.clear()
                self._offset_hist.clear()
                self._offset_ns = 0
                self._rtt_ns = None
                self._last_frame_mono = None

            threading.Thread(target=self._tcp_recv_loop, daemon=True).start()
            threading.Thread(target=self._udp_sync_loop, daemon=True).start()

            self._info("Quest connected")
            self._notify_state(True, "connected")
        except Exception as e:
            self._warn(f"Connect failed: {e}")
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    def stop(self) -> None:
        """Tear down the connection and stop the worker threads."""
        self._disconnect("transport stop")

    # ---------- internals ----------

    def _disconnect(self, reason: str) -> None:
        with self._lock:
            was_connected = self._connected
            self._running = False
            self._connected = False
            sock = self._tcp_sock
            self._tcp_sock = None

        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass

        if was_connected:
            self._warn(f"Quest disconnected: {reason}")
            self._notify_state(False, reason)

    def _tcp_recv_loop(self) -> None:
        buf = ""
        recv_sz = 1024 * 1024

        while True:
            with self._lock:
                if not self._running or self._tcp_sock is None:
                    break
                sock = self._tcp_sock

            try:
                try:
                    raw = sock.recv(recv_sz)
                    if not raw:
                        self._disconnect("server closed")
                        break
                    chunk = raw.decode("utf-8", errors="ignore")
                except BlockingIOError:
                    time.sleep(0.001)
                    continue

                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    pc_mono_ns = time.monotonic_ns()
                    now_mono = pc_mono_ns / 1e9

                    with self._lock:
                        self._last_frame_mono = now_mono
                        self._frame_times.append(now_mono)
                        if len(self._frame_times) > self._fps_window:
                            self._frame_times.pop(0)

                    frame = QuestFrame(
                        device_time_ns=int(msg.get("ovrTimeNs", 0) or 0),
                        pc_monotonic_ns=pc_mono_ns,
                        seq=0,
                        quest_id="",
                        delta_time_s=_safe_float(msg.get("deltaTime")),
                        raw=msg,
                    )

                    try:
                        self._on_frame(frame)
                    except Exception as e:
                        self._warn(f"on_frame callback raised: {e}")

            except Exception as e:
                self._disconnect(f"tcp error: {e}")
                break

    def _udp_sync_loop(self) -> None:
        if not self._ip:
            return

        quest_addr = (self._ip, self._sync_port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(("", self._sync_port))
            sock.settimeout(1.0)
            mono_ns = time.monotonic_ns

            while True:
                with self._lock:
                    if not self._running:
                        break

                try:
                    t1 = mono_ns()
                    sock.sendto(struct.pack("<BQ", 1, t1), quest_addr)
                    data, _ = sock.recvfrom(32)
                    t4 = mono_ns()

                    id_, t1_echo, t2_q = struct.unpack("<BQQ", data)
                    if id_ != 2:
                        continue

                    rtt = t4 - t1_echo
                    off = t2_q - ((t1_echo + t4) // 2)

                    if rtt < self._rtt_accept_ns:
                        with self._lock:
                            self._rtt_ns = rtt
                            self._offset_hist.append(int(off))
                            self._offset_hist[:] = self._offset_hist[
                                -self._offset_history :
                            ]
                            self._offset_ns = int(statistics.median(self._offset_hist))

                except socket.timeout:
                    pass
                except Exception:
                    time.sleep(0.5)
                time.sleep(1.0)
        finally:
            try:
                sock.close()
            except Exception:
                pass

    # ---------- logging helpers ----------

    def _info(self, msg: str) -> None:
        if self._log is not None:
            self._log.info(msg)

    def _warn(self, msg: str) -> None:
        if self._log is not None:
            self._log.warn(msg)

    def _notify_state(self, connected: bool, reason: str) -> None:
        if self._on_state_change is None:
            return
        try:
            self._on_state_change(connected, reason)
        except Exception as e:
            self._warn(f"on_state_change callback raised: {e}")


def _safe_float(value: Any) -> float:
    """Return float(value) or NaN if value is missing or non-numeric."""
    if value is None:
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")
