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
