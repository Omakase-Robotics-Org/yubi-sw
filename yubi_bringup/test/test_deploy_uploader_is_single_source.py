"""The S3 uploader must exist once, and provisioning must install that one.

Until 2026-08-13 there were three copies of ``yubi_s3_direct.py``. The canonical
one grew task partitioning (``task=<slug>/`` in the destination key, so ingest
can partition raw recordings by task with a cheap LIST); the two copies were
made before that and never followed. ``deploy/new-machine/Makefile`` installs
the uploader sitting next to *itself*, which was one of the stale copies — so
every machine provisioned with ``make s3-creds`` uploaded task-less keys and had
all of its episodes pooled under the fallback task, silently and forever.

Nothing detected it: the copies are not imported, so no test or linter ever
looked at them, and the upload itself succeeds.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
UPLOADER = "yubi_s3_direct.py"
#: The marker of the task-partitioning uploader (deploy/yubi_s3_direct.py).
TASK_PARTITION_MARKER = "task="


def _uploaders():
    return sorted(p for p in REPO.rglob(UPLOADER) if ".git" not in p.parts)


def test_exactly_one_uploader_in_the_repo():
    found = _uploaders()
    assert found == [REPO / "deploy" / UPLOADER], (
        "the uploader must have exactly one home (deploy/), because a copy "
        "silently stops receiving changes the original gets; found: "
        + ", ".join(str(p.relative_to(REPO)) for p in found)
    )


def test_the_one_uploader_partitions_by_task():
    """Pins the property the stale copies lacked, so a revert is caught."""
    src = (REPO / "deploy" / UPLOADER).read_text()
    assert TASK_PARTITION_MARKER in src, (
        "deploy/yubi_s3_direct.py no longer stamps task= into the destination "
        "key — raw recordings would stop being partitionable by task"
    )


LEGACY_SRC = (
    "org=o/site=s/location=l/date=2026-08-01/robot_type=yubi/"
    "robot_id=R1/ts=T/uuid=U/file.mcap"
)
CANONICAL_SRC = (
    "org=o/site=s/location=l/date=2026-08-19/task=5fd1975442564640a407660efba45fd8/"
    "robot_type=yubi/robot_id=R1/ts=T/uuid=U/file.mcap"
)


def _uploader_module():
    import importlib.util

    import pytest
    pytest.importorskip("boto3")
    spec = importlib.util.spec_from_file_location(
        "yubi_s3_direct_under_test", REPO / "deploy" / UPLOADER
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_canonical_sources_are_never_double_tasked():
    """A canonical source key (task= after date=) must upload VERBATIM.

    On 2026-08-19 yubi2's new data-backend started writing canonical keys and
    the uploader still inserted its legacy task=<slug> after robot_id= — 132
    S3 objects landed with TWO task= segments, a layout omakase-data-infra
    task_routing does not produce or expect.
    """
    u = _uploader_module()
    assert u.is_canonical(CANONICAL_SRC)
    assert not u.is_canonical(LEGACY_SRC)
    # strip_task must preserve the canonical segment (it is part of the
    # SOURCE key), and must undo exactly what insert_task added to a legacy key.
    assert u.strip_task(CANONICAL_SRC) == CANONICAL_SRC
    legacy_dst = u.insert_task(LEGACY_SRC, "some-slug")
    assert legacy_dst.count("task=") == 1
    assert u.strip_task(legacy_dst) == LEGACY_SRC
    # And the historical double-task shape maps back to its canonical source.
    double = u.insert_task(CANONICAL_SRC, "some-slug")
    assert u.strip_task(double) == CANONICAL_SRC


def test_upload_loop_guards_on_is_canonical():
    """The main loop must branch on is_canonical before inserting a task."""
    src = (REPO / "deploy" / UPLOADER).read_text()
    assert "is_canonical(src)" in src, (
        "the upload/gc paths no longer check is_canonical — canonical sources "
        "would get a second task= segment again"
    )


def test_provisioning_installs_the_canonical_uploader():
    """The Makefile must not install a file that merely sits beside it."""
    mk = (REPO / "deploy" / "new-machine" / "Makefile").read_text()
    install_lines = [ln for ln in mk.splitlines()
                     if UPLOADER in ln and "install" in ln]
    assert install_lines, "provisioning no longer installs the uploader at all"
    for ln in install_lines:
        # Resolve what the recipe points at, relative to the Makefile's dir.
        assert re.search(r"\.\./" + re.escape(UPLOADER), ln) or "deploy/" in ln, (
            "provisioning installs an uploader from its own directory rather "
            "than the canonical deploy/ copy — this is exactly the 2026-08-13 "
            "regression: " + ln.strip()
        )
