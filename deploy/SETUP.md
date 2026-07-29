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
Run the installer from the checkout — do **not** hand-copy, and do not edit the
Desktop copy in place (that is how yubi1/yubi2/yubi3 ended up with three
different launchers by 2026-07-29):

```sh
./deploy/install-launcher.sh
```

It installs `deploy/start-yubi.sh` → `~/Desktop/start-yubi.sh`, the `.desktop`
entry to both `~/Desktop/` and `~/.local/share/applications/`, marks it trusted
for GNOME, and pins it to the dock. Existing copies are backed up as
`*.bak-<timestamp>`, never deleted. It is idempotent — re-run it after every
`git pull` that touches the launcher.

- `Exec=` needs no per-box editing: it is `/bin/bash -lc "exec ~/Desktop/start-yubi.sh"`,
  and the script auto-detects the yubi-sw/yubi-app stack dirs (nested
  `~/projects/yubi-sw/yubi-sw` and flat `~/projects/yubi-sw` both work).
- What double-clicking it does: (0) take a `flock` single-instance lock and exit
  if a start is already in progress → (1) prompt Quest IP if unreachable →
  (2) restart yubi-sw + yubi-app docker stacks → (3) warn if the 6000pro LAN
  sync link is down → (4) wait for :3000 → (5) open **2 browser windows**:
  recording UI `localhost:3000/web` + dashboard `localhost:3000/web/dashboard`.
- The `flock` guard is load-bearing: the dock entry never matches a window, so
  GNOME launches a fresh copy on every click. On 2026-07-29 four copies started
  within two seconds on yubi1 and their racing `docker compose down` / `up -d`
  repeatedly destroyed `yubi_core`.

## Data path
yubi<N> collect → local MinIO → `~/yubi_s3_direct.py` → S3 `omakase-robotics-data`
(`task=` partitioned) → data-infra convert → HF. (No 6000pro LAN dependency for
upload; each box uploads its own data directly with its IoT cert.)
