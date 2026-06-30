# Provisioning a NEW YUBI data-collection machine

Reproducible setup for a fresh YUBI data-collection laptop (Meta Quest + a new
Ubuntu 22.04 laptop), reverse-engineered from the working **yubi1** machine.

**Scope:** starts AFTER Ubuntu 22.04 (Jammy) is flashed and you have a sudo user.
Flashing the OS is out of scope.

> This is a DRAFT. Every step is grounded in what `yubi1` actually runs; lines
> marked **(inferred)** are best-guess and need a confirming run on a real new box.

---

## 0. What a YUBI node actually is

`yubi1` is a TUXEDO Pulse-15 laptop running Ubuntu 22.04.5. The data-collection
stack is **all in Docker** (no host ROS install — `ROS_DISTRO` is empty on the
host; ROS 2 Jazzy lives inside the `yubi`/`yubi-core` images). Two compose stacks
plus a per-machine S3 uploader:

| Piece | What it is | Where |
|---|---|---|
| `yubi-sw` stack | bringup: cameras, encoders, footpedal/Quest input, ROS 2, **MinIO** (local S3, bucket `data`), web_video_server, lock_server | `~/projects/yubi-sw/yubi-sw` (this repo), `docker compose up -d` |
| `yubi-app` stack | operator web app: Go backend `:8000`, frontend `:3000`, postgres, redis, localstack | `~/projects/yubi-app/yubi-app`, `make up` |
| Quest app | `YubiQuestApp` APK sideloaded onto the headset; streams controller/tracking to the `airoa_quest` ROS bridge at `quest_ip` | Meta Quest headset |
| S3 uploader | cron `*/5` reads local MinIO and streams episodes to AWS S3 `omakase-robotics-data` using **AWS IoT X.509 creds** (no static keys) | `~/yubi_s3_direct.{py,sh}` + `~/iot/` (NOT yet in any repo — see §6) |

The grippers each carry a Seeed XIAO **ESP32C6** running AS5601 encoder firmware
(`firmware/ESP32C6_AS5601/`), flashed once per board via Arduino IDE. Hardware
firmware flashing is a one-time bench step, documented in the repo README, not
automated here.

---

## 1. Quick path

```bash
# 1. clone this repo with submodules (yubi-core is a submodule)
git clone --recursive https://github.com/Omakase-Robotics-Org/yubi-sw.git
cd yubi-sw/deploy/new-machine     # this Makefile lives here

# 2. one shot: apt deps + docker + adb + per-host config scaffolding
make install

# 3. human-in-the-loop steps Make CANNOT do for you (it will print these):
#    - calibrate encoders / write udev rules   (GUI, needs grippers plugged in)
#    - install the IoT device cert for S3       (per-device secret, §6)
#    - sideload the Quest APK                    (headset in dev mode over USB)

# 4. verify
make verify
```

`make install` runs `apt python docker adb config` (NOT `recording-stack`, which
builds large images, nor `s3-creds`, which needs a human to drop in a cert).
Build + bring up the stack explicitly:

```bash
make recording-stack    # build yubi/yubi-core images + docker compose up -d
make s3-creds           # interactive: guides cert install, writes uploader + cron
```

---

## 2. Ordered setup (post-flash → working node)

### Step 1 — base packages & Docker (`make apt docker`)
Installs git, make, curl, build tooling, `zenity`/`libnotify-bin` (the one-click
launcher uses them), `isc-dhcp-server` (optional wired-Quest DHCP), Docker CE +
the compose plugin, and adds you to the `docker` group.

> yubi1 runs Docker 29.5.3 with compose v5.1.4 (the bundled `docker compose`
> plugin, NOT the old `docker-compose` v1). **(inferred: exact CE version not
> pinned — install current CE.)**

After this step **log out/in** (or `newgrp docker`) so group membership applies.

### Step 2 — Python tooling (`make python`)
The host needs `python3` (3.10 ships with Jammy — fine) plus `boto3` for the S3
uploader, and `uv`/`uvx` for the repo's `make lint`/`make test`. yubi1's uploader
runs under the system `python3` with `boto3` available.

### Step 3 — adb for Quest sideloading (`make adb`)
Installs `android-tools-adb`. Used once to push the APK to the headset.

### Step 4 — clone app repos & scaffold config (`make config`)
Clones `yubi-app` next to `yubi-sw`, copies `.env.example` → `.env`, and creates
the gitignored `yubi_bringup/config/local/` overrides (Quest IP, backend API key)
from templates. **You must edit these** — see Step 7.

### Step 5 — encoder calibration + udev rules (MANUAL, GUI)
With both grippers plugged in over USB:
```bash
cd ../../tools           # yubi-sw/tools
sudo -E bash yubi_udev_setup.sh            # add --variant portable on the portable rig
```
A **Yubi Device Setup** GUI opens: confirm left/right camera + encoder
assignment, capture each gripper's closed (MIN) position, APPLY. This writes
`/etc/yubi/encoder_limits.yaml` and `/etc/udev/rules.d/99-yubi.rules`
(`/dev/yubi_*` symlinks). Replug devices or `sudo udevadm trigger` afterward.

This step is **not automated** — it needs a human to verify camera/encoder
sides and physically close each gripper. `make verify` checks the outputs exist.

Optional, documented in the repo README, not in this Makefile:
- `sudo ./tools/yubi_dhcp_setup.sh setup <nic> ...` — wired USB-Ethernet DHCP for the Quest.
- `sudo ./tools/yubi_lid_setup.sh apply` — keep recording with the laptop lid shut.

### Step 6 — build & launch the recording stack (`make recording-stack`)
```bash
make recording-stack
```
Runs (in `yubi-sw`): `make docker` (builds `yubi-core:latest` + `yubi:latest`,
generates `config/_runtime/<variant>/`), then `docker compose up -d`. Then brings
up `yubi-app` (`make up`). MinIO comes up inside the `yubi-sw` stack with bucket
`data` auto-created by `minio-init`.

### Step 7 — per-host config (MANUAL edits)
Edit the gitignored local overrides created in Step 4:
```yaml
# yubi_bringup/config/local/robot_config.yaml
/**:
  ros__parameters:
    api_key: "<API key issued by yubi-app for this robot>"
    base_url: "http://localhost:8000/api"
```
```yaml
# yubi_bringup/config/local/yubi_devices.yaml
quest_bridge_node:
  ros__parameters:
    quest_ip: "<Quest IP shown on the headset's YUBI app screen>"
```
Issue the API key in the web app (`http://localhost:3000/web`): create a robot →
issue API key → create + assign a task. Re-run `make build-config` (in `yubi-sw`)
after editing.

### Step 8 — Quest app (MANUAL sideload)
1. Get the APK. yubi1 has `yubi-quest-app-v0.1.0.apk` (cached under
   `yubi-app/apk/`). The repo README also documents:
   `wget https://releases.dev.airoa.io/yubi/quest-app/yubi-quest-app-v0.1.0.apk`
2. Enable Developer Mode on the headset: Meta Horizon mobile app → *Menu →
   Devices → your headset → Headset Settings → Developer Mode → on*. Plug the
   headset in over USB, accept *Allow USB debugging* inside the headset.
3. `adb install -r yubi-quest-app-v0.1.0.apk` (or drag-drop via SideQuest).
   App shows under *Unknown Sources* as **YubiQuestApp**.

### Step 9 — S3 credential delivery (MANUAL cert, then `make s3-creds`)
See §6. Mint a NEW per-device IoT cert, drop the private key into `~/iot/`
(0600), then `make s3-creds` installs the uploader + cron.

### Step 10 — verify (`make verify`)
See §5.

### Step 11 — one-click operator launcher (optional)
Copy the operator startup assets from `deploy/yubi1/` to `~/Desktop/`
(`start-yubi.sh`, `Start-YUBI.desktop`), `chmod +x`, and
`gio set ~/Desktop/Start-YUBI.desktop metadata::trusted true`. See
`deploy/yubi1/README.md`. It restarts both stacks, prompts for the Quest IP if
unreachable, and opens the recording UI.

---

## 3. Network / data path (context, from yubi1)

- **Quest → laptop:** Wi-Fi (or wired USB-Ethernet DHCP). The `airoa_quest` ROS
  bridge connects to `quest_ip`.
- **Episode upload (in-stack):** yubi-core uploads finished episodes to the
  **local MinIO** (bucket `data`), per `upload_targets.yaml`.
- **MinIO → AWS S3:** the `*/5` cron uploader streams MinIO objects to AWS S3
  `omakase-robotics-data` (ap-northeast-1). This is the laptop-direct path; the
  old 6000pro hop is decommissioned.
- **Tailscale** is installed on yubi1 (`tailscaled` running) for remote access.
  **(inferred: optional for the data path; install if you want SSH/remote ops.)**

---

## 4. Software versions (from yubi1)

| Component | Version on yubi1 |
|---|---|
| OS | Ubuntu 22.04.5 LTS (Jammy), kernel 6.8 |
| Host python | 3.10.12 (`/usr/bin/python3`) |
| ROS 2 | **none on host** — Jazzy inside Docker images |
| Docker | 29.5.3 |
| docker compose | v5.1.4 (plugin) |
| adb | present (`/usr/bin/adb`) |
| GPU | none (CPU-only video encode on this laptop) |

No NVIDIA / nvidia-container-toolkit on yubi1 — a data-collection laptop does not
need a GPU. Skip GPU setup unless your new box has one and a workload needs it.

---

## 5. Verifier (`make verify`)

`make verify` is the acceptance gate. It checks, and prints PASS/FAIL for each:

1. `docker` works and the compose plugin is present.
2. `python3 -c "import boto3"` succeeds.
3. `adb` is on PATH.
4. `/etc/yubi/encoder_limits.yaml` and `/etc/udev/rules.d/99-yubi.rules` exist
   (Step 5 was run).
5. The `yubi-sw` + `yubi-app` containers are up (`minio`, `yubi`, `yubi_core`,
   `yubi-app-backend-1`, `yubi-app-frontend-1`).
6. MinIO answers and bucket `data` exists.
7. The web UI answers on `http://localhost:3000/web`.
8. **S3 reachability** (only if `~/iot/` cert present): runs
   `IOT_*` env + `yubi_s3_direct.py --test`, which fetches temporary AWS creds
   via the IoT endpoint and confirms both AWS S3 and local MinIO are reachable.
   This is the end-to-end proof the cert + role alias work — **without printing
   any secret** (it prints only object key-counts).
9. The `*/5` uploader cron line is installed.

A new node is "done" when all of 1–9 PASS.

---

## 6. S3 credential delivery (the hard part)

### How yubi1 does it (verified)

yubi1 has **no `~/.aws` directory and no static AWS keys**. The uploader
(`~/yubi_s3_direct.py`) authenticates to AWS S3 using the **AWS IoT credentials
provider** (X.509 mutual-TLS → temporary STS creds via a role alias):

```
GET https://<IOT_ENDPOINT>/role-aliases/<ROLE_ALIAS>/credentials
    header: x-amzn-iot-thingname: <THING>
    TLS client cert: ~/iot/device.cert.pem + ~/iot/device.private.key
    CA:              ~/iot/AmazonRootCA1.pem
  -> { accessKeyId, secretAccessKey, sessionToken }   (temporary)
```

Non-secret config baked as defaults in the uploader (these are identifiers, not
secrets — safe to commit):
- `IOT_ENDPOINT` = `c7365kceqmnid.credentials.iot.ap-northeast-1.amazonaws.com`
- `IOT_ROLE_ALIAS` = `yubi-uploader-alias`
- `IOT_THING` = `yubi1`  ← **must be unique per device; override per machine**
- `S3_BUCKET` = `omakase-robotics-data`, region `ap-northeast-1`

The only secret on disk is `~/iot/device.private.key` (perms **0600**). The cert
(`device.cert.pem`) and `AmazonRootCA1.pem` are public. There are NO long-lived
AWS keys anywhere.

Critical gotcha (already handled in the uploader): IoT-vended creds break the
default boto3 ≥1.36 streaming checksum, so the client is built with
`Config(request_checksum_calculation="when_required", response_checksum_validation="when_required")`.

### Recommended: mint a NEW per-device cert for the new machine

Do this from a trusted admin box with AWS access to the `omakase-robotics`
account (use `aws sso login --profile omakase-robotics`). **One cert per
machine** — never copy yubi1's key to the new box.

```bash
THING=yubi2                       # unique per device
ROLE_ALIAS=yubi-uploader-alias    # reuse the existing alias (already maps to the uploader IAM role)
REGION=ap-northeast-1

# 1. create + activate a new cert/key pair (admin box, in a tmpdir)
aws iot create-keys-and-certificate --set-as-active --region $REGION \
  --certificate-pem-outfile device.cert.pem \
  --public-key-outfile device.public.key \
  --private-key-outfile device.private.key
#   note the certificateArn from the output

# 2. register the new Thing and attach the cert
aws iot create-thing --thing-name "$THING" --region $REGION
aws iot attach-thing-principal --thing-name "$THING" --principal "<certificateArn>" --region $REGION

# 3. attach a policy that allows iot:AssumeRoleWithCertificate for the role alias
#    (clone the policy yubi1's cert uses; scope it to this role alias)
aws iot attach-policy --policy-name yubi-uploader-policy --target "<certificateArn>" --region $REGION

# 4. grab the Amazon Root CA (public)
curl -o AmazonRootCA1.pem https://www.amazontrust.com/repository/AmazonRootCA1.pem
```

> The exact policy name / role-alias-target IAM role on yubi1 can be read back
> with `aws iot list-thing-principals --thing-name yubi1` →
> `aws iot list-attached-policies --target <arn>` and
> `aws iot describe-role-alias --role-alias yubi-uploader-alias`. Mirror those
> for the new Thing. **(inferred: policy name `yubi-uploader-policy` — confirm
> against yubi1's attached policy.)**

### Install on the new machine (private key never leaves a trusted channel)

Transfer the three files to the new box over SSH/scp (or paste the key via
`ssh new-box 'umask 077; cat > ~/iot/device.private.key'` from stdin — never
echo it to a terminal that logs). Then:

```bash
mkdir -p ~/iot && chmod 700 ~/iot
# place device.cert.pem, device.private.key, AmazonRootCA1.pem in ~/iot
chmod 600 ~/iot/device.private.key
chmod 644 ~/iot/device.cert.pem ~/iot/AmazonRootCA1.pem
# set the per-device thing name (so the uploader doesn't claim to be yubi1):
export IOT_THING=yubi2     # also bake into yubi_s3_direct.sh on this box
```

`make s3-creds` does the perms + cron wiring for you and reminds you to set
`IOT_THING`; it will **refuse to run** if `~/iot/device.private.key` is missing
(it never creates or fetches a key itself).

### Alternatives (and why IoT wins here)

| Option | How | Tradeoff |
|---|---|---|
| **AWS IoT cert (recommended)** | per-device X.509 → STS via role alias | ✅ no static keys, per-device revocable (deactivate the cert), already wired on yubi1. ❌ one-time admin setup per device. |
| AWS SSO / Identity Center | `aws sso login` on the box | Good for humans, bad for an unattended `*/5` cron — tokens expire and need interactive re-login. Wrong tool for a headless uploader. |
| Sealed secret / static IAM key | drop an IAM access key on the box | Simple, but a long-lived credential on a roaming laptop is the exact risk IoT avoids; rotation + blast radius are bad. Only as a last resort, scoped to PutObject on one bucket prefix. |

### Security rules (enforced by the Makefile + this doc)
- `device.private.key` is **0600**, `~/iot` is **0700**.
- **Never** commit any cert/key (`~/iot/` is outside the repo; add to
  `.gitignore` if you ever stage it inside the tree).
- **Never** print/echo/log the private key or the vended STS creds. The uploader
  and `make verify` only ever print object key-counts and status.
- One cert per machine. To retire a node, **deactivate/revoke its cert** in IoT —
  do not just delete the file.

---

## 7. What I could NOT verify (open questions)

1. **IoT policy name + role-alias IAM role.** I confirmed the *mechanism*
   (endpoint, role alias `yubi-uploader-alias`, thing `yubi1`, cert paths) from
   the uploader source on yubi1, but did NOT query AWS IoT for the exact attached
   policy name or the role behind the alias (no AWS calls made; read-only box
   inventory only). Confirm before minting `yubi2`'s cert.
2. **Quest APK distribution for a new org build.** yubi1 has `v0.1.0` cached and
   the README points at `releases.dev.airoa.io`. Whether Omakase ships its own
   build / where the canonical APK lives for new machines is unconfirmed.
3. **Quest pairing specifics** beyond sideload — the `quest_ip` flow is clear,
   but first-time headset account/enterprise enrollment is not covered here.
4. **MinIO duplication on yubi1.** yubi1 has BOTH a host `minio server /data`
   process AND the compose `minio` container on :9000 — likely a leftover/probe.
   A clean new box should run **only the compose MinIO** (what the uploader and
   yubi-core target). Flagged so the new node doesn't replicate the host process.
5. **`XRoboToolkit-Teleop-Sample-Python`** in `~/Programs` on yubi1 is NOT part
   of the running stack (the live Quest bridge is the in-repo `airoa_quest` ROS
   package). Treated as a leftover experiment; excluded from this setup.
6. **Tailscale necessity** for the data path — present on yubi1 but the upload is
   laptop-direct to AWS; install only for remote ops.
7. Exact Docker CE patch version is not pinned on yubi1; this guide installs
   current CE.
