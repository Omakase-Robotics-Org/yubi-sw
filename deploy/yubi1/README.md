# yubi1 operator files

**The startup launcher no longer lives here.** `deploy/yubi1/start-yubi.sh` and
`deploy/yubi1/Start-YUBI.desktop` were a yubi1-specific fork with hard-coded
`/home/omakase1/...` paths. They have been folded into the single box-agnostic
launcher and removed (recoverable from git history):

- canonical launcher: **`deploy/start-yubi.sh`**
- canonical `.desktop`: **`deploy/Start-YUBI.desktop`**
- install it on any box: **`./deploy/install-launcher.sh`**

See `deploy/SETUP.md` §4. What remains in this directory is genuinely
yubi1-specific: the S3 uploader.

## Files

- `yubi_s3_direct.py` / `yubi_s3_direct.sh` — **S3 uploader** (laptop-direct). Reads
  recorded episodes from the local MinIO via its S3 API and streams each object to
  AWS S3 (`omakase-robotics-data`) using short-lived creds from the AWS IoT
  credentials provider (X.509 mutual TLS — no static AWS keys on the device). See
  the S3 uploader section below.

## S3 uploader (yubi1 → S3, direct)

The on-site episodes are stored in the local MinIO in erasure-coded form (each
`*.mcap` is an object, not a raw file on disk), so the uploader reads them through
the **local MinIO S3 API** (`http://127.0.0.1:9000`, bucket `data`; MinIO creds are
auto-discovered from `docker-compose.yml` + `.env`) and streams object→AWS S3 (no
temp files → no local disk pressure). Dedupe state in `~/.yubi_s3_uploaded.json`
(key→size); on first run it seeds the state from objects already in S3 so it never
re-uploads. IoT creds are refetched on expiry so long backfills don't stall.

**One-time setup on yubi1**
1. Place the IoT device cert/key + Amazon root CA (downloaded once from AWS IoT for
   thing `yubi1`) — **NOT committed here, they are secrets**:
   ```
   ~/iot/device.cert.pem      (0644)
   ~/iot/device.private.key   (0600)
   ~/iot/AmazonRootCA1.pem    (0644)
   ```
   IoT setup: endpoint `c7365kceqmnid.credentials.iot.ap-northeast-1.amazonaws.com`,
   thing `yubi1`, role-alias `yubi-uploader-alias` (role `yubi-uploader-role`,
   PutObject-only on the bucket).
2. `pip install --user boto3`.
3. Cron (incremental, every 5 min; `flock` makes it single-instance):
   ```
   */5 * * * * /home/omakase1/yubi_s3_direct.sh >> /home/omakase1/yubi_s3_direct.log 2>&1
   ```

**Gotcha:** boto3 ≥ 1.36 enables default CRC checksums via aws-chunked encoding,
which breaks streaming uploads here with `UnseekableStreamError`. The uploader sets
`Config(request_checksum_calculation="when_required")` to avoid it — keep that.

The S3 key preserves the MinIO object key (the `org/site/location/date/.../uuid/*.mcap`
partition path), so S3 mirrors the collection partition layout for downstream ingest.

## Canonical deployment

The launcher's source of truth is `deploy/start-yubi.sh` in this repo; the Desktop
copies on every box are installed from it by `deploy/install-launcher.sh`. Never
edit `~/Desktop/start-yubi.sh` directly — change the repo copy, pull, re-run the
installer.

## GNOME requirements

`deploy/install-launcher.sh` already does the `chmod +x` and the
`gio set ... metadata::trusted true` that GNOME needs before it will treat the
Desktop file as a trusted, double-clickable app icon rather than a text file.

`Terminal=false` in the `.desktop` file is **intentional** — the operator should not
see a terminal window. The script logs to `~/yubi-start.log` and reports via desktop
notifications instead. (For debugging, run `~/Desktop/start-yubi.sh` from a terminal
manually.)

## Quest IP note

The Quest headset IP is configured in
`yubi_bringup/config/local/yubi_devices.yaml` (`quest_ip:`). It only changes when the
Wi-Fi/network changes. `start-yubi.sh` pings the configured IP on startup and, if it
is unreachable, prompts (zenity) the operator to enter the current IP — shown on the
Quest's YUBI-app screen — and updates the config before bringing the stack up.
