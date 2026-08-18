"""Disk protection must stay wired in — a full disk crashes the box.

On 2026-08-13 a recording on yubi1 was never stopped; its single .mcap grew to
253GB over four days and the disk hit 95% (the box had already crashed once
this way). Nothing watched free space, nothing capped a recording, and local
MinIO copies of episodes already uploaded to S3 were kept forever (70GB on
yubi2). These tests pin the three defenses added on 2026-08-18: the cron disk
guard, its installation, and the uploader's verified local GC.
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUARD = "yubi-disk-guard.sh"


def test_exactly_one_disk_guard_in_the_repo():
    found = sorted(p for p in REPO.rglob(GUARD) if ".git" not in p.parts)
    assert found == [REPO / "deploy" / GUARD], (
        "the disk guard must have exactly one home (deploy/); found: "
        + ", ".join(str(p.relative_to(REPO)) for p in found)
    )


def test_installer_installs_the_guard_with_a_cron():
    src = (REPO / "deploy" / "install-launcher.sh").read_text()
    assert GUARD in src and "crontab" in src, (
        "deploy/install-launcher.sh no longer installs the disk guard cron — "
        "nothing would stop the next runaway recording from filling the disk"
    )


def test_guard_covers_orphans_runaways_and_thresholds():
    src = (REPO / "deploy" / GUARD).read_text()
    for marker, why in [
        ("KEEP_HOURS", "orphaned recordings would pile up forever"),
        ("RUNAWAY_GB", "a never-stopped recording would fill the disk again"),
        ("CRIT_GB", "nothing would react before the disk is 100% full"),
    ]:
        assert marker in src, f"disk guard lost its {marker} defense — {why}"


def test_uploader_gc_is_verified_and_off_by_default():
    src = (REPO / "deploy" / "yubi_s3_direct.py").read_text()
    assert "YUBI_GC_DAYS" in src, "the uploader lost its local GC"
    assert "head_object" in src, (
        "the GC no longer HEAD-verifies an object is really in AWS S3 before "
        "deleting the local copy — state-file trust alone is not enough"
    )
    assert '"YUBI_GC_DAYS", "0"' in src, (
        "GC must stay opt-in (default 0/off): a box without the env var set "
        "must never delete local data"
    )


def test_wrapper_templates_enable_gc():
    for tpl in ("deploy/new-machine/yubi_s3_direct.sh",
                "deploy/yubi1/yubi_s3_direct.sh"):
        assert "YUBI_GC_DAYS" in (REPO / tpl).read_text(), (
            f"{tpl} no longer enables the local GC — that box's MinIO would "
            "grow unbounded again"
        )
