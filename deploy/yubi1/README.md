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

## Host profile (2026-07-29: portable → stationary)

yubi1 runs **`ROBOT_VARIANT=stationary`**. `.env` and `yubi_bringup/config/local/*.yaml`
are gitignored (they are the per-host layer), so the box's real settings cannot be
committed as-is and would be lost to a reimage. `config-local/` here is a snapshot
of them:

| box file (gitignored) | snapshot |
|---|---|
| `.env` → `ROBOT_VARIANT=stationary` | this section |
| `yubi_bringup/config/local/yubi_devices.yaml` | `config-local/yubi_devices.yaml` |
| `yubi_bringup/config/local/recording_gate.yaml` | `config-local/recording_gate.yaml` |
| `yubi_bringup/config/local/robot_config.yaml` | **not snapshotted — contains the backend API key** |

Restore after a reimage:

```sh
sed -i 's/^ROBOT_VARIANT=.*/ROBOT_VARIANT=stationary/' .env
cp deploy/yubi1/config-local/*.yaml yubi_bringup/config/local/
# then re-add config/local/robot_config.yaml by hand: api_key + base_url
# (http://localhost:8000/api), use_recording_gate: false, auto_repeat_episode: false,
# and the two provenance pins below
```

`config/local/robot_config.yaml` also pins the recording provenance (2026-07-29).
It is the one local file not snapshotted here, because it holds the backend API
key, so these two lines have to be re-added by hand:

```yaml
    runner_organization: "omakase-robotics"   # -> org=omakase-robotics
    site: "tokyo"                             # -> site=tokyo
```

`location:` is deliberately left empty so it resolves from the backend — it is
the field the web UI manages, and the robot is assigned to location `Meguro`
(-> `location=meguro`). The key is ordered org / site / location, broad to
narrow: site is the region, location is the office.

Why pinned rather than left to the backend:

- `site` had no backend resolution at all until yubi-core#2, so it was writing
  `site=unknown` into every object key.
- `runner_organization` does resolve from the backend, but when the backend is
  unreachable at `record_manager` startup it falls back to `"unknown"` — which
  is where the 20 objects under `org=unknown` came from.
- An explicit config value always wins, so the key is identical before and after
  the `yubi-core` image is rebuilt with #2. One prefix boundary, not two.

The backend rows were renamed to agree with the pins (organization
`Omakase Robotics`, site `Tokyo`, location `Meguro`, which normalise to the same key
segments), so the two layers cannot drift apart.

Two notes on why the local layer looks the way it does:

- **`gripper_double_click_node` / `portable_joy_command_node` presence markers.**
  The launch registry spawns a node only when its key appears in a merged
  `yubi_devices.yaml`, and those two keys live in `config/portable/`. The operators
  accept/reject episodes with the gripper double-click, so a stationary yubi1 still
  needs them. They are set per-host rather than added to `config/stationary/`
  because a real yagura stationary box drives the task state machine from the
  footpedal; making the glove a second input for *every* stationary box is a
  behaviour change, not a config fix.
- **Merge gotcha:** `list + list` means **b replaces a**. A plain list in
  `config/local/` wipes a variant's `__extend__` additions — extend, don't replace.

## Recording provenance (`org=` / `site=` / `location=` in the object key)

Every recording's object key is
`org=…/site=…/location=…/date=…/task=…/robot_type=…/robot_id=…/ts=…/uuid=…`, built
from `meta.json`, which `record_manager` fills from `robot_config.yaml` plus
`GET /robot/me`. Two ways to set them:

1. **Web UI (`:3000`)** — Locations page creates/renames locations; the robot edit
   dialog assigns the robot to one. The organization name is what the `org=` segment
   comes from; it is *not* editable in the web UI today (see the fleet notes), so it
   stays at whatever the DB was seeded with.
2. **`config/local/robot_config.yaml`** — an explicit `site:`, `location:` or
   `runner_organization:` always wins over the backend. `"FIXME"` (org, robot_type)
   and `""` (site, location) mean "ask the backend".

**These values are read once, when `record_manager` starts.** Changing them in the
UI does not affect a running stack — restart `yubi_core` (`docker compose restart
yubi_core`) and check the startup log line `Resolved deployment metadata: …` before
recording.

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
