"""The device-setup launcher must exist once and never hardcode a variant.

yubi2's hand-made Desktop copy of the calibration wrapper hardcoded
``--variant portable``. When the box switched to the stationary profile on
2026-07-29 the wrapper was not in any repo, so nothing updated it, and every
calibration attempt after that failed the camera-count gate (exit=3 on
2026-08-03; a human had to bypass the launcher by hand on 2026-08-13).
The canonical copy reads ``ROBOT_VARIANT`` from the stack ``.env`` instead,
so a profile switch is one committed line, not a per-box Desktop edit.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRAPPER = "yubi-device-setup.sh"


def _wrappers():
    return sorted(p for p in REPO.rglob(WRAPPER) if ".git" not in p.parts)


def test_exactly_one_device_setup_wrapper_in_the_repo():
    found = _wrappers()
    assert found == [REPO / "deploy" / WRAPPER], (
        "the device-setup wrapper must have exactly one home (deploy/); "
        "found: " + ", ".join(str(p.relative_to(REPO)) for p in found)
    )


def test_wrapper_never_hardcodes_a_variant():
    """The variant must come from config, or the next profile switch breaks it."""
    src = (REPO / "deploy" / WRAPPER).read_text()
    calls = [ln for ln in src.splitlines() if "yubi_udev_setup.sh" in ln
             and not ln.lstrip().startswith("#")]
    assert calls, "the wrapper no longer runs tools/yubi_udev_setup.sh at all"
    for ln in calls:
        assert not re.search(r"--variant\s+(stationary|portable)\b", ln), (
            "deploy/%s passes a literal variant to yubi_udev_setup.sh — it "
            "must pass the value resolved from the stack .env: %s"
            % (WRAPPER, ln.strip())
        )
    assert "ROBOT_VARIANT" in src, (
        "the wrapper no longer resolves ROBOT_VARIANT from the stack .env"
    )


def test_start_yubi_integrates_the_wrapper():
    """start-yubi must be able to open calibration itself (the one entry point)."""
    src = (REPO / "deploy" / "start-yubi.sh").read_text()
    assert WRAPPER in src, (
        "deploy/start-yubi.sh no longer references the device-setup wrapper — "
        "operators would be back to a separate, forgettable calibration step"
    )


def test_installer_installs_the_wrapper():
    src = (REPO / "deploy" / "install-launcher.sh").read_text()
    assert WRAPPER in src and "YUBI-Device-Setup.desktop" in src, (
        "deploy/install-launcher.sh no longer installs the device-setup "
        "launcher — boxes would drift back to hand-made Desktop copies"
    )


def test_variant_switch_backs_up_env_before_writing():
    """The startup menu's variant switch must never edit .env without a backup.

    ROBOT_VARIANT in .env is the single source of truth for the whole stack
    (compose overlays, calibration GUI, launchers); a botched in-place edit
    with no backup would take the box down with nothing to roll back to.
    """
    src = (REPO / "deploy" / "start-yubi.sh").read_text()
    assert "構成タイプ" in src, (
        "start-yubi lost the variant-switch menu the operator manual promises"
    )
    fn = src.split("choose_variant()", 1)
    assert len(fn) == 2, "choose_variant() is gone from start-yubi.sh"
    body = fn[1]
    backup_pos = body.find('.env.bak-')
    write_pos = body.find("sed -i")
    assert 0 <= backup_pos < write_pos, (
        "choose_variant must cp .env to a .env.bak-<timestamp> BEFORE the "
        "sed that rewrites ROBOT_VARIANT"
    )
