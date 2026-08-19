#!/usr/bin/env python3
"""yubi -> S3 DIRECT uploader (edge laptop, no 6000pro hop).

Box instance is NOT baked into this code: the specific box's identity (its AWS
IoT thing name / cert) comes from env (IOT_THING + IOT_CERT/KEY/CA), so the same
script runs on any yubi box. The device *type* is "yubi"; the "1" (box number)
lives only in that box's env/cert.

Reads episode objects from the LOCAL MinIO via its S3 API (objects are
erasure-coded on disk, so the filesystem can't be read directly), and streams
each one to AWS S3 (omakase-robotics-data) using temporary creds from the AWS
IoT credentials provider (X.509 mTLS, no static AWS keys). Stream MinIO->AWS
(no temp files -> no local disk pressure). Dedupe by SOURCE key+size; refresh
AWS creds on expiry so a long backfill doesn't stall.

TASK PARTITION (2026-07-04): the destination S3 key gets a ``task=<slug>/``
segment inserted after ``robot_id=…/`` so downstream can partition raw by task
with a cheap LIST (no per-recording meta.json GET). The task is read from each
recording's ``meta.json`` ``episode.label`` (airoa v2), slugified. Dedupe tracks
the MinIO SOURCE key (not the rewritten dest), and the S3 seed strips ``task=``
back to the source key — so enabling this does NOT re-upload / duplicate the
recordings already in S3 under the old (task-less) keys.

Env:
  IOT_CERT / IOT_KEY / IOT_ROOT_CA   yubi1 device cert (default ~/iot/*)
  IOT_ENDPOINT / IOT_THING / IOT_ROLE_ALIAS
  S3_BUCKET        omakase-robotics-data
  MINIO_ENDPOINT   http://127.0.0.1:9000
  MINIO_BUCKET     data
  YUBI_SW_DIR      ~/projects/yubi-sw/yubi-sw   (for MinIO cred discovery)
  STATE_FILE       ~/.yubi_s3_uploaded.json
  YUBI_GC_DAYS     unset/0 = keep everything (default). N>0 = after the upload
                   pass, delete local MinIO objects older than N days whose
                   presence in AWS S3 is re-verified by a HEAD (key+size match)
                   right before each delete — the dedupe state alone is never
                   trusted for a deletion. Recent objects always survive as a
                   local recovery buffer. (Added 2026-08-18: local MinIO grew
                   unbounded — 70GB on yubi2 — and full disks crash the boxes.)
Flags: --test (reachability), --dry-run (list would-upload + dest keys, NO write).
"""
import datetime
import os, sys, re, json, glob, time, ssl, urllib.request
import boto3
from botocore.config import Config

EP    = os.environ.get("IOT_ENDPOINT", "c7365kceqmnid.credentials.iot.ap-northeast-1.amazonaws.com")
THING = os.environ.get("IOT_THING", "")  # box instance thing name — REQUIRED via env
ALIAS = os.environ.get("IOT_ROLE_ALIAS", "yubi-uploader-alias")
CERT  = os.path.expanduser(os.environ.get("IOT_CERT", "~/iot/device.cert.pem"))
KEY   = os.path.expanduser(os.environ.get("IOT_KEY",  "~/iot/device.private.key"))
CA    = os.path.expanduser(os.environ.get("IOT_ROOT_CA", "~/iot/AmazonRootCA1.pem"))
AWS_BUCKET   = os.environ.get("S3_BUCKET", "omakase-robotics-data")
MINIO_EP     = os.environ.get("MINIO_ENDPOINT", "http://127.0.0.1:9000")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "data")
SW_DIR = os.path.expanduser(os.environ.get("YUBI_SW_DIR", "~/projects/yubi-sw/yubi-sw"))
STATE  = os.path.expanduser(os.environ.get("STATE_FILE", "~/.yubi_s3_uploaded.json"))
GC_DAYS = int(os.environ.get("YUBI_GC_DAYS", "0") or 0)

# boto3>=1.36 default CRC (aws-chunked) breaks streaming uploads here -> when_required
_AWSCFG = Config(request_checksum_calculation="when_required",
                 response_checksum_validation="when_required",
                 retries={"max_attempts": 3, "mode": "standard"})
_MINIOCFG = Config(signature_version="s3v4", connect_timeout=5, read_timeout=60,
                   retries={"max_attempts": 2})

# --- task-partition helpers -------------------------------------------------
# Two key generations coexist in the fleet (2026-08):
#   legacy    org=…/date=…/robot_type=…/robot_id=…/ts=…       (no task in key)
#   canonical org=…/date=…/task=<id>/robot_type=…/robot_id=…/ts=…
# The canonical task= segment is written by yubi-core's data-backend
# (canonical_path.py) and is what omakase-data-infra task_routing keys on.
# This uploader only synthesizes a task segment for LEGACY sources (inserted
# after robot_id=); a canonical source is uploaded VERBATIM — inserting again
# produced double-task keys on 2026-08-19 (132 objects, repaired by hand).
_PREFIX_RE = re.compile(r"(.*?/uuid=[^/]+/)")       # recording dir (holds meta.json)
_AFTER_RID = re.compile(r"(.*?/robot_id=[^/]+/)(.*)")
_CANON_TASK_RE  = re.compile(r"/task=[^/]+/robot_type=")   # canonical position
_LEGACY_TASK_RE = re.compile(r"(/robot_id=[^/]+)/task=[^/]+")


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s or "untagged"


def strip_task(key: str) -> str:
    """S3 key -> SOURCE key: drop only the legacy inserted segment.

    Legacy dest  …/robot_id=X/task=slug/ts=…  -> …/robot_id=X/ts=…
    Canonical    …/date=D/task=id/robot_type=…/ts=…  (kept verbatim: the
    canonical task= after date= is part of the source key itself).
    """
    return _LEGACY_TASK_RE.sub(r"\1", key, count=1)


def is_canonical(key: str) -> bool:
    """True when the source key already carries the canonical task= segment."""
    return bool(_CANON_TASK_RE.search(key))


def recording_prefix(key: str):
    m = _PREFIX_RE.match(key)
    return m.group(1) if m else None


def insert_task(key: str, slug: str) -> str:
    """Insert ``task=<slug>/`` right after the ``robot_id=…/`` segment."""
    m = _AFTER_RID.match(key)
    if m:
        return f"{m.group(1)}task={slug}/{m.group(2)}"
    return key  # unexpected layout: leave as-is (never silently mis-place)


def iot_creds():
    url = f"https://{EP}/role-aliases/{ALIAS}/credentials"
    ctx = ssl.create_default_context(cafile=CA); ctx.load_cert_chain(CERT, KEY)
    req = urllib.request.Request(url, headers={"x-amzn-iot-thingname": THING})
    with urllib.request.urlopen(req, context=ctx, timeout=20) as r:
        return json.load(r)["credentials"]


def aws_client():
    c = iot_creds()
    return boto3.client("s3", region_name="ap-northeast-1", config=_AWSCFG,
                        aws_access_key_id=c["accessKeyId"],
                        aws_secret_access_key=c["secretAccessKey"],
                        aws_session_token=c["sessionToken"])


def minio_client():
    env = {}
    for ef in glob.glob(SW_DIR + "/**/.env", recursive=True)[:20]:
        for ln in open(ef, errors="ignore"):
            m = re.match(r'\s*([A-Z_][A-Z0-9_]*)\s*=\s*"?([^"\n]+)', ln)
            if m: env.setdefault(m.group(1), m.group(2).strip())
    rs = lambda v: env.get(re.match(r'\$\{?([A-Z_][A-Z0-9_]*)', v or "").group(1), v) if re.match(r'\$\{?[A-Z_]', v or "") else v
    t = open(SW_DIR + "/docker-compose.yml", errors="ignore").read()
    u = rs(re.search(r'MINIO_ROOT_USER[:=]\s*"?([^\s"\']+)', t).group(1))
    p = rs(re.search(r'MINIO_ROOT_PASSWORD[:=]\s*"?([^\s"\']+)', t).group(1))
    return boto3.client("s3", endpoint_url=MINIO_EP, aws_access_key_id=u,
                        aws_secret_access_key=p, config=_MINIOCFG)


def task_for(local, prefix, cache):
    """Slugified episode.label for a recording prefix (cached per recording)."""
    if prefix in cache:
        return cache[prefix]
    slug = "untagged"
    try:
        body = local.get_object(Bucket=MINIO_BUCKET, Key=prefix + "meta.json")["Body"].read()
        slug = slugify(((json.loads(body) or {}).get("episode") or {}).get("label"))
    except Exception:
        pass
    cache[prefix] = slug
    return slug


def gc_local(local, aws, aws_t, done, tcache, dry):
    """Delete local MinIO objects that are verifiably in AWS S3 and old enough.

    Two passes over one listing snapshot: candidates are collected (and their
    task slugs resolved, while each recording's meta.json still exists) before
    anything is deleted, and within a recording meta.json goes LAST — otherwise
    a half-collected recording could no longer resolve its own dest key.
    Every delete is gated on a fresh HEAD to AWS (task= key first, then the
    legacy task-less key) with a size match; a miss means the object is kept.
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=GC_DAYS)
    cands, kept = [], 0
    for page in local.get_paginator("list_objects_v2").paginate(Bucket=MINIO_BUCKET):
        for o in page.get("Contents", []):
            src, sz = o["Key"], o["Size"]
            if o["LastModified"] > cutoff or done.get(src) != sz:
                kept += 1; continue
            if is_canonical(src):
                slug = None                          # HEAD the source key verbatim
            else:
                prefix = recording_prefix(src)
                slug = task_for(local, prefix, tcache) if prefix else "untagged"
            cands.append((src, sz, slug))
    cands.sort(key=lambda c: c[0].endswith("meta.json"))  # meta.json of each recording last
    n_del, freed = 0, 0
    for src, sz, slug in cands:
        if time.time() - aws_t > 2400:
            aws = aws_client(); aws_t = time.time(); print("[creds] refreshed", flush=True)
        in_s3 = False
        keys = (src,) if slug is None else (insert_task(src, slug), src)
        for key in keys:
            try:
                if aws.head_object(Bucket=AWS_BUCKET, Key=key)["ContentLength"] == sz:
                    in_s3 = True; break
            except Exception:
                pass
        if not in_s3:
            kept += 1; continue
        if dry:
            n_del += 1; freed += sz; continue
        try:
            local.delete_object(Bucket=MINIO_BUCKET, Key=src)
            n_del += 1; freed += sz
        except Exception as e:
            print(f"[gc-ERR] {src[:80]}: {str(e)[:120]}", flush=True)
    verb = "would delete" if dry else "deleted"
    print(f"[gc] {verb} {n_del} local objects ({freed/1e9:.2f}GB) uploaded >{GC_DAYS}d ago; kept {kept}", flush=True)


def main():
    for f in (CERT, KEY, CA):
        if not os.path.exists(f): sys.exit(f"missing cert: {f}")
    dry = "--dry-run" in sys.argv
    local = minio_client()
    aws = aws_client(); aws_t = time.time()
    print("[creds] IoT temp AWS creds OK", flush=True)
    if "--test" in sys.argv:
        n = aws.list_objects_v2(Bucket=AWS_BUCKET, MaxKeys=1).get("KeyCount", 0)
        m = local.list_objects_v2(Bucket=MINIO_BUCKET, MaxKeys=1).get("KeyCount", 0)
        print(f"[test] AWS reachable (keycount~{n}); MinIO '{MINIO_BUCKET}' reachable (keycount~{m})")
        return
    try: done = json.loads(open(STATE).read()).get("done", {})
    except Exception: done = {}
    if not done:   # seed from S3; strip task= so dest keys map back to SOURCE keys
        try:
            ap = aws.get_paginator("list_objects_v2")
            for pg in ap.paginate(Bucket=AWS_BUCKET):
                for o in pg.get("Contents", []):
                    done[strip_task(o["Key"])] = o["Size"]
            print(f"[seed] {len(done)} objects already in S3 -> skip", flush=True)
        except Exception as e:
            print(f"[seed] skip-seed ({str(e)[:80]})", flush=True)
    n_up = n_skip = 0
    tcache: dict = {}
    paginator = local.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=MINIO_BUCKET):
        for o in page.get("Contents", []):
            src, sz = o["Key"], o["Size"]           # SOURCE (MinIO) key = dedupe key
            if sz == 0 or done.get(src) == sz:
                n_skip += 1; continue
            if is_canonical(src):
                slug, dst = "(canonical)", src      # key already carries task= — upload verbatim
            else:
                prefix = recording_prefix(src)
                slug = task_for(local, prefix, tcache) if prefix else "untagged"
                dst = insert_task(src, slug)        # legacy source: DEST key gains task=<slug>
            if dry:
                n_up += 1
                if n_up <= 20:
                    print(f"[would-up] task={slug} :: {dst[:110]}", flush=True)
                continue
            if time.time() - aws_t > 2400:
                aws = aws_client(); aws_t = time.time(); print("[creds] refreshed", flush=True)
            try:
                body = local.get_object(Bucket=MINIO_BUCKET, Key=src)["Body"]
                aws.upload_fileobj(body, AWS_BUCKET, dst)
                done[src] = sz; n_up += 1
                if n_up % 10 == 0:
                    json.dump({"done": done, "ts": int(time.time())}, open(STATE, "w"))
                print(f"[up] task={slug} :: {dst[:100]} ({sz/1e6:.1f}MB)", flush=True)
            except Exception as e:
                print(f"[ERR] {src[:80]}: {str(e)[:150]}", flush=True)
    if dry:
        print(f"[dry-run] would upload {n_up}, skip {n_skip} (already in S3); {len(done)} tracked")
    else:
        json.dump({"done": done, "ts": int(time.time())}, open(STATE, "w"))
        print(f"[done] uploaded {n_up}, skipped {n_skip}; {len(done)} tracked", flush=True)
    if GC_DAYS > 0:
        gc_local(local, aws, aws_t, done, tcache, dry)


if __name__ == "__main__":
    main()
