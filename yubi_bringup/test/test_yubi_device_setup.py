"""Unit tests for the yubi device-setup helpers (tools/yubi_device_select_gui.py).

Covers the pure helpers used by `tools/yubi_udev_setup.sh` via its GUI:

- parse_line              — serial-line parser (equivalence partitioning + boundary values)
- usb_hub_of              — USB topology parent lookup (equivalence + boundary)
- validate_cam_count      — variant ↔ camera-count gate (equivalence + boundary)
- assign_camera_roles     — L/R/center auto-assignment (condition branching matrix)
- resolve_camera_pick     — pulldown auto-swap (condition branching)
- can_apply_portable      — portable APPLY pre-flight (condition branching truth table)
- build_apply_payload     — JSON schema contract (variant branch)

The GUI module lives outside yubi_bringup/, in repo-root tools/, so we add
that path to sys.path the same way test_build_runtime_configs.py does for its
sibling tool.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import yubi_device_select_gui as gds  # noqa: E402


# ---------------------------------------------------------------------------
# TestParseLine — equivalence partitioning + boundary values
# ---------------------------------------------------------------------------
class TestParseLine:
    """parse_line(bytes) -> (side, value)

    Partitions:
      - prefix: L/l, R/r, unknown letter, no prefix, empty prefix
      - body:   positive, negative, zero, non-numeric, missing
      - shape:  empty, whitespace-only, comment, multi-comma
    """

    @pytest.mark.parametrize(
        "line,expected",
        [
            # --- labelled, normal ----------------------------------------
            (b"L001,0.5", ("left", 0.5)),
            (b"R001,0.5", ("right", 0.5)),
            # lowercase prefix is accepted (condition: head[:1] ∈ {"L","l"})
            (b"l001,0.5", ("left", 0.5)),
            (b"r001,0.5", ("right", 0.5)),
            # --- unlabelled ----------------------------------------------
            (b"0.5", (None, 0.5)),
            (b"-0.5", (None, -0.5)),
            # --- boundary numeric values ---------------------------------
            (b"L001,-0.5", ("left", -0.5)),
            (b"L001,0", ("left", 0.0)),
            (b"L001,1e3", ("left", 1000.0)),  # scientific notation passes float()
            # --- empty / whitespace / comment boundaries -----------------
            (b"", (None, None)),
            (b"  ", (None, None)),
            (b"\n", (None, None)),
            (b"# comment", (None, None)),
            # --- numeric parse failures ----------------------------------
            (b"L001,abc", ("left", None)),
            (b"abc", (None, None)),
            # --- unknown / empty label heads -----------------------------
            (b"X001,0.5", (None, 0.5)),  # unknown letter ⇒ side=None
            (b",0.5", (None, 0.5)),  # empty head (head[:1] == "")
            (b" L , 0.5 ", ("left", 0.5)),  # whitespace tolerated
            # --- multi-comma boundary: split(maxsplit=1) keeps tail intact
            (b"L001,1,0.5", ("left", None)),  # "1,0.5" is not a float
        ],
    )
    def test_partitions(self, line, expected):
        assert gds.parse_line(line) == expected


# ---------------------------------------------------------------------------
# TestUsbHubOf — equivalence partitioning + boundary + condition branching
# ---------------------------------------------------------------------------
class TestUsbHubOf:
    """usb_hub_of(loc) branches:
    1) loc is falsy           -> None
    2) "." in loc             -> rsplit by "."
    3) "-" in loc             -> split by "-"
    4) bare value             -> identity
    """

    @pytest.mark.parametrize(
        "loc,expected",
        [
            # Branch 2: dotted forms
            ("1-1.4.2", "1-1.4"),
            ("1-1.4", "1-1"),
            ("2-1.10.3", "2-1.10"),
            # Branch 3: dashed only
            ("1-2", "1"),
            ("3-4", "3"),
            # Branch 4: identity fallback
            ("1", "1"),
            ("usb", "usb"),
            # Branch 1: falsy inputs
            ("", None),
            (None, None),
        ],
    )
    def test_branches(self, loc, expected):
        assert gds.usb_hub_of(loc) == expected


# ---------------------------------------------------------------------------
# TestValidateCamCount — exhaustive equivalence + boundary on (variant, count)
# ---------------------------------------------------------------------------
class TestValidateCamCount:
    @pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
    def test_stationary(self, count):
        err = gds.validate_cam_count("stationary", count)
        if count == 2:
            assert err is None
        else:
            assert err is not None and "stationary" in err and "2" in err

    @pytest.mark.parametrize("count", [0, 1, 2, 3, 4])
    def test_portable(self, count):
        err = gds.validate_cam_count("portable", count)
        if count == 3:
            assert err is None
        else:
            assert err is not None and "portable" in err and "3" in err

    def test_unknown_variant_reports_variant_name(self):
        err = gds.validate_cam_count("foo", 2)
        assert err is not None
        assert "foo" in err
        # Lists the supported variants so the user can recover.
        assert "stationary" in err and "portable" in err


# ---------------------------------------------------------------------------
# TestCamSidesFor — sanity check on the variant -> side-tuple mapping
# ---------------------------------------------------------------------------
class TestCamSidesFor:
    def test_stationary(self):
        assert gds.cam_sides_for("stationary") == ("left", "right")

    def test_portable_includes_center(self):
        assert gds.cam_sides_for("portable") == ("left", "right", "center")

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            gds.cam_sides_for("foo")


# ---------------------------------------------------------------------------
# TestAssignCameraRoles — full condition-branch matrix
# ---------------------------------------------------------------------------
class TestAssignCameraRoles:
    """Decision table over (variant, L_hub_match, R_hub_match, ordering)."""

    def test_portable_all_match_default_order(self):
        """Hubs line up with default A,B,C ordering."""
        cams = [("A", "hA"), ("B", "hB"), ("C", "hC")]
        res = gds.assign_camera_roles(cams, "hA", "hB", "portable")
        assert res == {
            "left": "A",
            "right": "B",
            "center": "C",
            "matched_left": True,
            "matched_right": True,
        }

    def test_portable_all_match_swapped_order(self):
        """L/R swapped relative to enumeration order; center is the leftover."""
        cams = [("A", "hB"), ("B", "hA"), ("C", "hC")]
        res = gds.assign_camera_roles(cams, "hA", "hB", "portable")
        assert res["left"] == "B"
        assert res["right"] == "A"
        assert res["center"] == "C"
        assert res["matched_left"] is True
        assert res["matched_right"] is True

    def test_portable_only_left_matches(self):
        cams = [("A", "hA"), ("B", "hX"), ("C", "hY")]
        res = gds.assign_camera_roles(cams, "hA", "hZ", "portable")
        assert res["left"] == "A"
        assert res["matched_left"] is True
        assert res["matched_right"] is False
        # R + center come from the leftover B,C in input order; both are present
        # and distinct from L.
        assert {res["right"], res["center"]} == {"B", "C"}

    def test_portable_only_right_matches(self):
        cams = [("A", "hX"), ("B", "hB"), ("C", "hY")]
        res = gds.assign_camera_roles(cams, "hZ", "hB", "portable")
        assert res["right"] == "B"
        assert res["matched_left"] is False
        assert res["matched_right"] is True
        assert {res["left"], res["center"]} == {"A", "C"}

    def test_portable_no_match_falls_back_to_input_order(self):
        cams = [("A", "hX"), ("B", "hY"), ("C", "hZ")]
        res = gds.assign_camera_roles(cams, "hQ", "hP", "portable")
        assert res == {
            "left": "A",
            "right": "B",
            "center": "C",
            "matched_left": False,
            "matched_right": False,
        }

    def test_portable_none_hubs_fall_back(self):
        """When either ESP hub is None, that side cannot match -> fallback."""
        cams = [("A", "hA"), ("B", "hB"), ("C", "hC")]
        res = gds.assign_camera_roles(cams, None, None, "portable")
        assert res["matched_left"] is False
        assert res["matched_right"] is False
        assert {res["left"], res["right"], res["center"]} == {"A", "B", "C"}

    def test_portable_l_hub_equals_r_hub_anomaly(self):
        """If L and R ESPs report the same hub, only L claims it (first match);
        R must then fall back. Center is the remaining camera."""
        cams = [("A", "hA"), ("B", "hB"), ("C", "hC")]
        res = gds.assign_camera_roles(cams, "hA", "hA", "portable")
        assert res["left"] == "A"
        assert res["matched_left"] is True
        # R cannot reuse A; matched_right must be False since the only hub match
        # (A) was already taken by L.
        assert res["matched_right"] is False
        assert res["right"] != "A"
        assert res["center"] != "A"
        assert res["right"] != res["center"]

    def test_stationary_basic(self):
        cams = [("A", "hA"), ("B", "hB")]
        res = gds.assign_camera_roles(cams, "hA", "hB", "stationary")
        assert res["left"] == "A"
        assert res["right"] == "B"
        assert res["center"] is None
        assert res["matched_left"] is True
        assert res["matched_right"] is True

    def test_stationary_swapped(self):
        cams = [("A", "hB"), ("B", "hA")]
        res = gds.assign_camera_roles(cams, "hA", "hB", "stationary")
        assert res["left"] == "B"
        assert res["right"] == "A"
        assert res["center"] is None

    def test_wrong_count_raises(self):
        with pytest.raises(ValueError):
            gds.assign_camera_roles([("A", "hA"), ("B", "hB")], "hA", "hB", "portable")
        with pytest.raises(ValueError):
            gds.assign_camera_roles(
                [("A", "hA"), ("B", "hB"), ("C", "hC")], "hA", "hB", "stationary"
            )

    def test_unknown_variant_raises(self):
        with pytest.raises(ValueError):
            gds.assign_camera_roles([("A", "hA")], "hA", "hA", "foo")

    def test_result_is_deterministic_for_repeated_calls(self):
        """Idempotency: same input -> same output (no hidden ordering on dicts)."""
        cams = [("A", "hA"), ("B", "hB"), ("C", "hC")]
        r1 = gds.assign_camera_roles(cams, "hA", "hB", "portable")
        r2 = gds.assign_camera_roles(cams, "hA", "hB", "portable")
        assert r1 == r2


# ---------------------------------------------------------------------------
# TestResolveCameraPick — pulldown auto-swap, condition branching
# ---------------------------------------------------------------------------
class TestResolveCameraPick:
    PORTABLE_SIDES = ("left", "right", "center")
    STATIONARY_SIDES = ("left", "right")

    def _portable_init(self):
        return {"left": "A", "right": "B", "center": "C"}

    def _stationary_init(self):
        return {"left": "A", "right": "B"}

    # ---- 3-slot table: pick X for slot S where X currently lives in slot T -
    @pytest.mark.parametrize(
        "side,new_dev,expected",
        [
            # left ← B   (swap with right)
            ("left", "B", {"left": "B", "right": "A", "center": "C"}),
            # left ← C   (swap with center)
            ("left", "C", {"left": "C", "right": "B", "center": "A"}),
            # right ← A  (swap with left)
            ("right", "A", {"left": "B", "right": "A", "center": "C"}),
            # right ← C  (swap with center)
            ("right", "C", {"left": "A", "right": "C", "center": "B"}),
            # center ← A (swap with left)
            ("center", "A", {"left": "C", "right": "B", "center": "A"}),
            # center ← B (swap with right)
            ("center", "B", {"left": "A", "right": "C", "center": "B"}),
        ],
    )
    def test_portable_swaps_two_slots(self, side, new_dev, expected):
        out = gds.resolve_camera_pick(
            self._portable_init(), side, new_dev, self.PORTABLE_SIDES
        )
        assert out == expected

    @pytest.mark.parametrize("side", PORTABLE_SIDES)
    def test_portable_pick_self_is_noop(self, side):
        init = self._portable_init()
        out = gds.resolve_camera_pick(init, side, init[side], self.PORTABLE_SIDES)
        assert out == init

    def test_portable_does_not_mutate_input(self):
        init = self._portable_init()
        snapshot = dict(init)
        gds.resolve_camera_pick(init, "left", "B", self.PORTABLE_SIDES)
        assert init == snapshot

    def test_portable_result_keeps_three_distinct_devices(self):
        """Auto-swap must keep all 3 slots assigned to distinct devs."""
        init = self._portable_init()
        for side in self.PORTABLE_SIDES:
            for new_dev in ("A", "B", "C"):
                out = gds.resolve_camera_pick(init, side, new_dev, self.PORTABLE_SIDES)
                vals = list(out.values())
                assert len(set(vals)) == 3, (
                    f"Picking {new_dev} for {side} produced non-distinct {out}"
                )

    # ---- Stationary: only 2 slots ---------------------------------------
    @pytest.mark.parametrize(
        "side,new_dev,expected",
        [
            ("left", "B", {"left": "B", "right": "A"}),
            ("right", "A", {"left": "B", "right": "A"}),
        ],
    )
    def test_stationary_swaps(self, side, new_dev, expected):
        out = gds.resolve_camera_pick(
            self._stationary_init(), side, new_dev, self.STATIONARY_SIDES
        )
        assert out == expected

    @pytest.mark.parametrize("side", STATIONARY_SIDES)
    def test_stationary_pick_self_is_noop(self, side):
        init = self._stationary_init()
        out = gds.resolve_camera_pick(init, side, init[side], self.STATIONARY_SIDES)
        assert out == init

    def test_invalid_side_raises(self):
        with pytest.raises(ValueError):
            gds.resolve_camera_pick(
                self._portable_init(), "head", "A", self.PORTABLE_SIDES
            )

    def test_unassigned_new_dev_sets_without_swap(self):
        """Edge case: new_dev is not in any slot (shouldn't happen via the UI
        since the Combobox is restricted to detected devs, but the helper
        must still produce a coherent result rather than crash)."""
        init = {"left": "A", "right": "B", "center": "C"}
        out = gds.resolve_camera_pick(init, "left", "Z", self.PORTABLE_SIDES)
        assert out["left"] == "Z"
        # The previous occupant ("A") is dropped — caller is expected to surface
        # this as a warning, but the helper does not mutate other slots.
        assert out["right"] == "B"
        assert out["center"] == "C"


# ---------------------------------------------------------------------------
# TestCanApplyPortable — condition branching: 2^4 truth table over
#   (L_label_seen, R_label_seen, center_set, all_distinct)
# ---------------------------------------------------------------------------
class TestCanApplyPortable:
    L_DEV = "/dev/ttyACM0"
    R_DEV = "/dev/ttyACM1"

    def _esp(self, l_seen: bool, r_seen: bool):
        return {
            self.L_DEV: "left" if l_seen else None,
            self.R_DEV: "right" if r_seen else None,
        }

    def _roles(self, center_set: bool, all_distinct: bool):
        if not center_set:
            return {"left": "/dev/video0", "right": "/dev/video2", "center": None}
        if all_distinct:
            return {
                "left": "/dev/video0",
                "right": "/dev/video2",
                "center": "/dev/video4",
            }
        # Duplicate: center equals left
        return {"left": "/dev/video0", "right": "/dev/video2", "center": "/dev/video0"}

    @pytest.mark.parametrize("l_seen", [True, False])
    @pytest.mark.parametrize("r_seen", [True, False])
    @pytest.mark.parametrize("center_set", [True, False])
    @pytest.mark.parametrize("all_distinct", [True, False])
    def test_truth_table(self, l_seen, r_seen, center_set, all_distinct):
        esp = self._esp(l_seen, r_seen)
        roles = self._roles(center_set, all_distinct)
        err = gds.can_apply_portable(esp, roles)
        if l_seen and r_seen and center_set and all_distinct:
            assert err is None
        else:
            assert err is not None and err  # non-empty error string

    def test_priority_label_missing_beats_center_missing(self):
        """When both labels AND center are missing, the label error wins (more
        actionable: user can't even start until grippers stream their side)."""
        esp = self._esp(False, False)
        roles = self._roles(center_set=False, all_distinct=True)
        err = gds.can_apply_portable(esp, roles)
        assert err is not None and "label" in err.lower()

    def test_label_error_lists_missing_devnodes(self):
        esp = self._esp(True, False)
        roles = self._roles(center_set=True, all_distinct=True)
        err = gds.can_apply_portable(esp, roles)
        assert err is not None
        assert self.R_DEV in err
        assert self.L_DEV not in err  # only the missing side gets listed

    def test_center_missing_message_mentions_center(self):
        esp = self._esp(True, True)
        roles = self._roles(center_set=False, all_distinct=True)
        err = gds.can_apply_portable(esp, roles)
        assert err is not None
        assert "center" in err

    def test_duplicate_devs_message_mentions_distinct(self):
        esp = self._esp(True, True)
        roles = self._roles(center_set=True, all_distinct=False)
        err = gds.can_apply_portable(esp, roles)
        assert err is not None
        assert "distinct" in err


# ---------------------------------------------------------------------------
# TestBuildApplyPayload — schema contract, variant branch
# ---------------------------------------------------------------------------
class TestBuildApplyPayload:
    L = "/dev/ttyACM0"
    R = "/dev/ttyACM1"

    def _common_kwargs(self, variant: str, with_center: bool):
        cam_role = {"left": "/dev/video0", "right": "/dev/video2"}
        if with_center:
            cam_role["center"] = "/dev/video4"
        return dict(
            cam_role=cam_role,
            esp_role={"left": self.L, "right": self.R},
            esp_serials={self.L: "SL", self.R: "SR"},
            calib_min={self.L: 1234.5, self.R: 6789.0},
            variant=variant,
        )

    def test_stationary_has_no_center_key(self):
        payload = gds.build_apply_payload(**self._common_kwargs("stationary", False))
        assert "center" not in payload["camera"]
        assert payload["camera"]["left"] == {"dev": "/dev/video0"}
        assert payload["camera"]["right"] == {"dev": "/dev/video2"}

    def test_portable_includes_center(self):
        payload = gds.build_apply_payload(**self._common_kwargs("portable", True))
        assert payload["camera"]["center"] == {"dev": "/dev/video4"}

    def test_esp_block_contract(self):
        payload = gds.build_apply_payload(**self._common_kwargs("stationary", False))
        assert payload["esp32"]["left"] == {
            "dev": self.L,
            "serial": "SL",
            "min": 1234.5,
        }
        assert payload["esp32"]["right"] == {
            "dev": self.R,
            "serial": "SR",
            "min": 6789.0,
        }

    def test_encoder_node_params_block(self):
        payload = gds.build_apply_payload(**self._common_kwargs("portable", True))
        assert payload["encoder_node"]["ros__parameters"] == {
            "left_min_raw": 1234.5,
            "right_min_raw": 6789.0,
        }


# ---------------------------------------------------------------------------
# Smoke test: the module must import without GUI deps (no cv2/serial/tk/PIL).
# This guards the lazy-import refactor — if someone moves an `import cv2` back
# to module scope, this test fails immediately on the CI box (where cv2 is
# usually absent from the test venv).
# ---------------------------------------------------------------------------
class TestModuleLoadsWithoutHeavyDeps:
    def test_helpers_callable_at_module_scope(self):
        # All helpers we depend on must be present and callable.
        assert callable(gds.parse_line)
        assert callable(gds.usb_hub_of)
        assert callable(gds.validate_cam_count)
        assert callable(gds.assign_camera_roles)
        assert callable(gds.resolve_camera_pick)
        assert callable(gds.can_apply_portable)
        assert callable(gds.build_apply_payload)
        # Module-level constants exist (the sh script's behaviour relies on
        # the same definitions, but we at least pin them on the Python side).
        assert gds.EXPECTED_CAM_COUNT == {"stationary": 2, "portable": 3}
        assert gds.SUPPORTED_VARIANTS == ("stationary", "portable")
