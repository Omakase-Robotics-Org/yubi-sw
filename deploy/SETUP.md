# Omakase YUBI collection-box setup

Per-box checklist to provision a yubi collection laptop (yubi1, yubi2, …). Run
through ALL of it for a new box — the numbered steps are the ones that have been
forgotten before (esp. **step 4, the Start shortcut**, was missed on yubi2).

Box identity is `yubi<N>`; nothing here is baked into code — the box number lives
only in env / the IoT cert. ssh aliases: `yubi1`, `yubi2`, …

## 0. Code + docker
- `git clone` **yubi-sw** and **yubi-app** from `Omakase-Robotics-Org` into
  `~/projects/` (layout may be flat `~/projects/yubi-sw` or nested
  `~/projects/yubi-sw/yubi-sw` — the scripts auto-detect either).
- Docker + `docker compose` installed and the user in the `docker` group.

## 1. AWS IoT identity (per box) — for S3 upload
- Create an IoT **thing `yubi<N>`** + a certificate, attach the yubi-uploader IoT
  policy (allows assuming role-alias `yubi-uploader-alias` →
  `yubi-uploader-role`, PutObject on `omakase-robotics-data`). Endpoint
  `c7365kceqmnid.credentials.iot.ap-northeast-1.amazonaws.com`.
- Put on the box (mode matters): `~/iot/device.cert.pem` (644),
  `~/iot/device.private.key` (**600**), `~/iot/AmazonRootCA1.pem` (644, same file
  on every box).
- Verify: fetch creds with `x-amzn-iot-thingname: yubi<N>` → temp creds returned.

## 2. S3 uploader
- `boto3` in the system python3. If `pip` is missing and `sudo` isn't available:
  `curl -fsSL https://bootstrap.pypa.io/get-pip.py | python3 - --user` then
  `python3 -m pip install --user boto3`.
- Copy `deploy/yubi_s3_direct.py` → `~/yubi_s3_direct.py`.
- Wrapper `~/yubi_s3_direct.sh` (flock single-instance) exporting:
  `IOT_CERT/IOT_KEY/IOT_ROOT_CA` (=~/iot/*), **`IOT_THING=yubi<N>`**,
  `YUBI_SW_DIR=<the yubi-sw dir that has docker-compose>` (for MinIO cred
  discovery), `S3_BUCKET=omakase-robotics-data`; then `python3 ~/yubi_s3_direct.py "$@"`.
- **cron** `*/5 * * * * ~/yubi_s3_direct.sh >> ~/yubi_s3_direct.log 2>&1`.
- Verify: `~/yubi_s3_direct.sh --test` → `[creds] IoT temp AWS creds OK`
  (MinIO part fails until the stack is up — that's fine). The uploader stamps
  `task=<slug>/` (from meta.json `episode.label`) into the S3 key.

## 3. Quest headset IP
- `yubi_bringup/config/local/yubi_devices.yaml` → `quest_ip`. The start script
  (step 4) prompts for it via a dialog whenever the Quest isn't reachable.

## 4. Start shortcut (Desktop) — ⚠ EASY TO FORGET (missed on yubi2)
- Copy `deploy/start-yubi.sh` → `~/Desktop/start-yubi.sh`; `chmod +x`.
  (It auto-detects the yubi-sw/yubi-app stack dirs, so no per-box path editing.)
- Copy `deploy/Start-YUBI.desktop` → `~/Desktop/Start-YUBI.desktop`; `chmod +x`;
  `gio set ~/Desktop/Start-YUBI.desktop metadata::trusted true`. Edit its `Exec=`
  to the box user's home if needed.
- What double-clicking it does: (1) prompt Quest IP if unreachable →
  (2) restart yubi-sw + yubi-app docker stacks → (3) wait for :3000 →
  (4) open **2 browser windows**: recording UI `localhost:3000/web` + dashboard
  `localhost:3000/web/dashboard`.

## Data path
yubi<N> collect → local MinIO → `~/yubi_s3_direct.py` → S3 `omakase-robotics-data`
(`task=` partitioned) → data-infra convert → HF. (No 6000pro LAN dependency for
upload; each box uploads its own data directly with its IoT cert.)
