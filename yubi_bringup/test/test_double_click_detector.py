"""Unit tests for DoubleClickDetector's closed-duration window.

The detector turns gripper open/close transitions into CLICK events. A real
grasp also opens and closes the gripper, so without an upper bound on how long
the gripper may stay closed, letting go of a grasped object emits a CLICK — and
two of those in a second start or stop an episode by accident.
"""

import sys
import types
from unittest.mock import MagicMock

import pytest

# double_click_detector imports rclpy.node at module scope; stub it so the pure
# detector logic can be tested without a ROS environment.
if "rclpy" not in sys.modules:
    rclpy_mod = types.ModuleType("rclpy")
    rclpy_node_mod = types.ModuleType("rclpy.node")
    rclpy_node_mod.Node = object
    rclpy_mod.node = rclpy_node_mod
    sys.modules["rclpy"] = rclpy_mod
    sys.modules["rclpy.node"] = rclpy_node_mod

from yubi_bringup.double_click_detector import (  # noqa: E402
    DoubleClickDetector,
    Event,
)

OPEN_WIDTH = 1.0
CLOSED_WIDTH = 0.0


@pytest.fixture()
def clock(monkeypatch):
    """Replace time.monotonic with a hand-advanced clock."""
    import time as time_mod

    from yubi_bringup import double_click_detector

    state = {"now": 100.0}
    monkeypatch.setattr(
        double_click_detector.time, "monotonic", lambda: state["now"]
    )
    assert time_mod is not None
    return state


def _close_then_open(detector, clock, hold_sec, node):
    """Drive one close→open cycle that stays closed for ``hold_sec``."""
    detector.update(CLOSED_WIDTH, node)
    clock["now"] += hold_sec
    return detector.update(OPEN_WIDTH, node)


@pytest.fixture()
def node():
    return MagicMock()


def test_short_press_is_a_click(clock, node):
    detector = DoubleClickDetector()
    events = _close_then_open(detector, clock, 0.1, node)
    assert Event.CLICK in events


def test_micro_spike_is_not_a_click(clock, node):
    """Below min_closed_sec — sensor noise, not an operator press."""
    detector = DoubleClickDetector()
    events = _close_then_open(detector, clock, 0.005, node)
    assert events == []


def test_long_hold_is_not_a_click(clock, node):
    """Regression: releasing a grasped object used to emit a CLICK, so two
    grasps within click_window_sec toggled an episode."""
    detector = DoubleClickDetector()
    events = _close_then_open(detector, clock, 2.0, node)
    assert events == []


def test_two_grasps_do_not_start_an_episode(clock, node):
    detector = DoubleClickDetector()
    _close_then_open(detector, clock, 2.0, node)
    clock["now"] += 0.1
    events = _close_then_open(detector, clock, 2.0, node)
    assert Event.DOUBLE_CLICK not in events
    assert detector.in_episode is False


def test_two_short_presses_start_an_episode(clock, node):
    detector = DoubleClickDetector()
    _close_then_open(detector, clock, 0.1, node)
    clock["now"] += 0.1
    events = _close_then_open(detector, clock, 0.1, node)
    assert Event.DOUBLE_CLICK in events
    assert Event.EPISODE_START in events
    assert detector.in_episode is True
