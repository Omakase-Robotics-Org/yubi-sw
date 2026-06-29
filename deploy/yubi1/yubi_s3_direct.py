#!/usr/bin/env python3
"""yubi1 -> S3 DIRECT uploader (edge laptop, no 6000pro hop).

Reads episode objects from the LOCAL MinIO via its S3 API (objects are
erasure-coded on disk, so the filesystem can't be read directly), and streams
each one to AWS S3 (omakase-robotics-data) using temporary creds from the AWS
IoT credentials provider (X.509 mTLS, no static AWS keys). Stream MinIO->AWS
(no temp files -> no local disk pressure). Dedupe by key+size; refresh AWS
creds on expiry so a long backfill doesn't stall.

Env:
  IOT_CERT / IOT_KEY / IOT_ROOT_CA   yubi1 device cert (default ~/iot/*)
  IOT_ENDPOINT / IOT_THING / IOT_ROLE_ALIAS
  S3_BUCKET        omakase-robotics-data
  MINIO_ENDPOINT   http://127.0.0.1:9000
  MINIO_BUCKET     data
  YUBI_SW_DIR      ~/projects/yubi-sw/yubi-sw   (for MinIO cred discovery)
  STATE_FILE       ~/.yubi_s3_uploaded.json
"""
import os, sys, re, json, glob, time, ssl, urllib.request
import boto3
from botocore.config import Config

EP    = os.environ.get("IOT_ENDPOINT", "c7365kceqmnid.credentials.iot.ap-northeast-1.amazonaws.com")
THING = os.environ.get("IOT_THING", "yubi1")
ALIAS = os.environ.get("IOT_ROLE_ALIAS", "yubi-uploader-alias")
CERT  = os.path.expanduser(os.environ.get("IOT_CERT", "~/iot/device.cert.pem"))
KEY   = os.path.expanduser(os.environ.get("IOT_KEY",  "~/iot/device.private.key"))
CA    = os.path.expanduser(os.environ.get("IOT_ROOT_CA", "~/iot/AmazonRootCA1.pem"))
AWS_BUCKET   = os.environ.get("S3_BUCKET", "omakase-robotics-data")
MINIO_EP     = os.environ.get("MINIO_ENDPOINT", "http://127.0.0.1:9000")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "data")
SW_DIR = os.path.expanduser(os.environ.get("YUBI_SW_DIR", "~/projects/yubi-sw/yubi-sw"))
STATE  = os.path.expanduser(os.environ.get("STATE_FILE", "~/.yubi_s3_uploaded.json"))

# boto3>=1.36 default CRC (aws-chunked) breaks streaming uploads here -> when_required
_AWSCFG = Config(request_checksum_calculation="when_required",
                 response_checksum_validation="when_required",
                 retries={"max_attempts": 3, "mode": "standard"})
_MINIOCFG = Config(signature_version="s3v4", connect_timeout=5, read_timeout=60,
                   retries={"max_attempts": 2})


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


def main():
    for f in (CERT, KEY, CA):
        if not os.path.exists(f): sys.exit(f"missing cert: {f}")
    local = minio_client()
    aws = aws_client(); aws_t = time.time()
    print("[creds] IoT temp AWS creds OK", flush=True)
    if "--test" in sys.argv:
        n = aws.list_objects_v2(Bucket=AWS_BUCKET, MaxKeys=1).get("KeyCount", 0)
        m = local.list_objects_v2(Bucket=MINIO_BUCKET, MaxKeys=1).get("KeyCount", 0)
        print(f"[test] AWS s3 reachable (keycount~{n}); local MinIO bucket '{MINIO_BUCKET}' reachable (keycount~{m})")
        return
    try: done = json.loads(open(STATE).read()).get("done", {})
    except Exception: done = {}
    if not done:   # seed from what's already in S3 (e.g. uploaded earlier from 6000pro)
        try:
            ap = aws.get_paginator("list_objects_v2")
            for pg in ap.paginate(Bucket=AWS_BUCKET):
                for o in pg.get("Contents", []):
                    done[o["Key"]] = o["Size"]
            print(f"[seed] {len(done)} objects already in S3 -> skip", flush=True)
        except Exception as e:
            print(f"[seed] skip-seed ({str(e)[:80]})", flush=True)
    n_up = n_skip = 0
    paginator = local.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=MINIO_BUCKET):
        for o in page.get("Contents", []):
            key, sz = o["Key"], o["Size"]
            if sz == 0 or done.get(key) == sz:
                n_skip += 1; continue
            if time.time() - aws_t > 2400:        # refresh creds ~every 40min
                aws = aws_client(); aws_t = time.time(); print("[creds] refreshed", flush=True)
            try:
                body = local.get_object(Bucket=MINIO_BUCKET, Key=key)["Body"]
                aws.upload_fileobj(body, AWS_BUCKET, key)
                done[key] = sz; n_up += 1
                if n_up % 10 == 0:
                    json.dump({"done": done, "ts": int(time.time())}, open(STATE, "w"))
                print(f"[up] {key[:80]} ({sz/1e6:.1f}MB)", flush=True)
            except Exception as e:
                print(f"[ERR] {key[:80]}: {str(e)[:150]}", flush=True)
    json.dump({"done": done, "ts": int(time.time())}, open(STATE, "w"))
    print(f"[done] uploaded {n_up}, skipped {n_skip}; {len(done)} tracked", flush=True)


if __name__ == "__main__":
    main()
