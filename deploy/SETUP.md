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

It installs `deploy/start-yubi.sh` → `~/Desktop/start-yubi.sh` **and**
`deploy/yubi-device-setup.sh` → `~/Desktop/yubi-device-setup.sh` (the
camera/encoder calibration wrapper), their `.desktop` entries to both
`~/Desktop/` and `~/.local/share/applications/`, marks them trusted for GNOME,
and pins Start-YUBI to the dock. Existing copies are backed up as
`*.bak-<timestamp>`, never deleted. It is idempotent — re-run it after every
`git pull` that touches the launchers.

- `Exec=` needs no per-box editing: it is `/bin/bash -lc "exec ~/Desktop/start-yubi.sh"`,
  and the script auto-detects the yubi-sw/yubi-app stack dirs (nested
  `~/projects/yubi-sw/yubi-sw` and flat `~/projects/yubi-sw` both work).
- What double-clicking Start-YUBI does: (0) take a `flock` single-instance lock
  and exit if a start is already in progress → (1) startup menu: normal start /
  change the Quest IP + variant (stationary/portable; backs up `.env`, rewrites
  `ROBOT_VARIANT`, and a change flows into recalibration) / recalibrate —
  auto-continues with a normal start after
  15 s → (2) open the calibration GUI when the saved udev rules / encoder
  origins are missing, were written for a different `ROBOT_VARIANT`, or the
  `/dev/yubi_*` devices are gone (USB re-plug) → (3) prompt Quest IP if
  unreachable → (4) if an episode is recording right now, ask before the forced
  restart → (5) restart yubi-sw + yubi-app docker stacks → (6) warn if the
  6000pro LAN sync link is down → (7) wait for :3000 → (8) open **2 browser
  windows**: recording UI `localhost:3000/web` + dashboard
  `localhost:3000/web/dashboard`. Calibration is therefore part of Start-YUBI —
  a re-imaged or re-plugged box heals on the next double-click; the separate
  YUBI-Device-Setup icon is just a direct entrance to the same wrapper.
- The calibration wrapper (`deploy/yubi-device-setup.sh`) resolves the variant
  from the stack's `.env` (`ROBOT_VARIANT`) — never hardcode a variant in a
  launcher (yubi2's hand-made Desktop copy did, and every calibration failed
  with exit=3 after the box switched profiles on 2026-07-29). It also
  hard-resets the gripper ESP32C6 boards first (USB-CDC wedge) and always
  brings the docker stack back up, even when the GUI fails.
- The `flock` guard is load-bearing: the dock entry never matches a window, so
  GNOME launches a fresh copy on every click. On 2026-07-29 four copies started
  within two seconds on yubi1 and their racing `docker compose down` / `up -d`
  repeatedly destroyed `yubi_core`.

## 5. Disk protection (automatic — installed by `install-launcher.sh`)
A full disk crashes the box (2026-08-13: an unstopped recording grew a single
253GB .mcap over four days on yubi1). Three defenses, none needing an operator:

- **`~/.local/bin/yubi-disk-guard.sh`** (cron `*/10`, installed with the
  launchers): deletes abandoned recordings under `~/yubi_data/rosbags` once
  they have been idle >48h (recent ones stay as a recovery buffer), stops and
  deletes a still-growing recording past 25GB (`docker restart yubi_core`),
  and below 80GB free warns the desktop + reclaims docker debris (dangling
  volumes / build cache). Thresholds via `YUBI_GUARD_*` env. Log:
  `~/yubi-disk-guard.log`.
- **Uploader GC**: with `YUBI_GC_DAYS=14` in `~/yubi_s3_direct.sh`, the
  uploader deletes local MinIO objects 14 days after their upload — each
  delete is gated on a fresh HEAD to AWS S3 (key+size match), never on the
  state file alone. Unset/0 disables.
- **start-yubi**: refuses to start below 30GB free (operator can override).

## Data path
yubi<N> collect → local MinIO → `~/yubi_s3_direct.py` → S3 `omakase-robotics-data`
(`task=` partitioned) → data-infra convert → HF. (No 6000pro LAN dependency for
upload; each box uploads its own data directly with its IoT cert.) Local MinIO
copies are GC'd 14 days after verified upload (§5); raw rosbag dirs are removed
by the normal flow within minutes, and abandoned ones by the disk guard.
