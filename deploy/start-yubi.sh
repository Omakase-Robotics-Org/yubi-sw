#!/usr/bin/env bash
# start-yubi.sh — one-click YUBI startup (works on any yubi box; auto-detects the
# yubi-sw / yubi-app stack dirs so nested (~/projects/yubi-sw/yubi-sw) and flat
# (~/projects/yubi-sw) layouts both work).
#   1. refuses to run twice at once (single-instance flock guard)
#   2. startup menu: normal start / change the Quest IP + variant / recalibrate
#      (auto-continues with a normal start after 15s, so an unattended
#      double-click still boots; a variant change backs up .env, rewrites
#      ROBOT_VARIANT, and flows into recalibration via the check below)
#   3. opens the calibration GUI (deploy/yubi-device-setup.sh) when the saved
#      udev rules / encoder origins are missing, were written for a different
#      variant, or the /dev/yubi_* devices are gone (re-plugged USB)
#   4. asks the Quest IP if the headset isn't reachable
#   5. asks before restarting if an episode is being recorded right now
#   6. (re)starts yubi-sw + yubi-app docker stacks
#   7. checks the 6000pro LAN sync link
#   8. waits until the web app answers on :3000
#   9. opens the recording UI + the dashboard (2 browser windows)
#
# CANONICAL COPY: deploy/start-yubi.sh in Omakase-Robotics-Org/yubi-sw.
# Install with deploy/install-launcher.sh — do not hand-edit the Desktop copy.
#
# Dry run:  ./start-yubi.sh --check   (or YUBI_START_DRYRUN=1)
#   Reports the detected stack dirs / Quest config and exits WITHOUT touching
#   docker, the Quest config or the browser. Still takes the flock, so it also
#   proves the single-instance guard. Use it to verify an install.
set -uo pipefail

DRYRUN="${YUBI_START_DRYRUN:-0}"
[ "${1:-}" = "--check" ] && DRYRUN=1

# Resolve the operator's home. $HOME is correct unless we were invoked under
# sudo, in which case $HOME is root's and we want the invoking user's.
REAL_HOME="$HOME"
if [ -n "${SUDO_USER:-}" ]; then
  _h="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6)"
  [ -n "$_h" ] && REAL_HOME="$_h"
fi
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="$REAL_HOME/yubi-start.log"

# --- single-instance guard -------------------------------------------------
# Start-YUBI.desktop is pinned to the GNOME dock, and the dock entry never
# matches a window (this script has no window of its own), so GNOME treats every
# click as "launch a new copy". Two copies racing "docker compose down" /
# "up -d" tear down each other's containers -- on 2026-07-29 four copies started
# inside two seconds on yubi1 and yubi_core was stopped and removed repeatedly.
# Take an exclusive lock; if another copy already holds it, tell the operator and
# exit rather than piling on.
LOCK="$REAL_HOME/.yubi-start.lock"
exec 9>"$LOCK"
if ! flock -n 9; then
  notify-send -u critical -i dialog-warning "YUBI" \
    "すでに起動処理が実行中です。完了までお待ちください。" 2>/dev/null || true
  echo "$(date '+%F %T') :: another start-yubi.sh already running - exiting" >> "$LOG"
  exit 0
fi
# ---------------------------------------------------------------------------

# Mirror everything to the log. Skipped in dry-run: the tee process substitution
# can lose the tail of the output when the script exits immediately after.
if [ "$DRYRUN" != 1 ]; then
  exec > >(tee -a "$LOG") 2>&1
fi
echo ""; echo "======== $(date '+%F %T') :: starting YUBI ========"
[ "$DRYRUN" = 1 ] || notify-send -i video-display "YUBI" "起動中… 数十秒お待ちください" 2>/dev/null || true

dc() { if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi; }
find_stack() { local d f; for d in "$@"; do for f in docker-compose.yml docker-compose.yaml compose.yaml compose.yml; do
  [ -f "$d/$f" ] && { echo "$d"; return 0; }; done; done; return 1; }
restart_stack() { local dir="$1"; [ -n "$dir" ] && [ -d "$dir" ] || { echo "!! stack dir not found"; return 1; }
  echo "--- restarting stack in $dir"; ( cd "$dir" && dc down; dc up -d ); }
wait_for_url() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o /dev/null "$url"
    return $?
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -q -O /dev/null "$url"
    return $?
  fi
  python3 - "$url" <<'PY' >/dev/null 2>&1
import sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1], timeout=2):
        pass
except Exception:
    raise SystemExit(1)
PY
}
open_url() {
  local url="$1"
  if [ -x "$REAL_HOME/Desktop/launch-google-chrome.sh" ]; then
    "$REAL_HOME/Desktop/launch-google-chrome.sh" "$url" >/dev/null 2>&1 &
    return 0
  fi
  xdg-open "$url" >/dev/null 2>&1 &
}

SW=$(find_stack \
  "$REAL_HOME/projects/yubi-sw" \
  "$REAL_HOME/projects/yubi-sw/yubi-sw" \
  "$REAL_HOME/Desktop/yubi-sw" \
  "$REAL_HOME/Desktop/yubi-sw-new" \
  "$SCRIPT_DIR/yubi-sw" \
  "$SCRIPT_DIR/yubi-sw-new")
APP=$(find_stack \
  "$REAL_HOME/projects/yubi-app" \
  "$REAL_HOME/projects/yubi-app/yubi-app" \
  "$REAL_HOME/Desktop/yubi-app")
QUEST_CFG=""
[ -n "$SW" ] && QUEST_CFG=$(find "$SW" -path "*config/local/yubi_devices.yaml" 2>/dev/null | head -1)
echo "detected: yubi-sw=$SW  yubi-app=$APP  quest_cfg=$QUEST_CFG"

# --- device calibration state ------------------------------------------------
# tools/yubi_udev_setup.sh (run via deploy/yubi-device-setup.sh) writes the udev
# rules and the encoder origins. After a re-image, a variant switch, or a USB
# re-plug into different ports, the saved state no longer matches and the stack
# would come up without its /dev/yubi_* devices — check BEFORE starting and
# offer the GUI here, instead of letting the recording gate fail later.
RULES_FILE=/etc/udev/rules.d/99-yubi-devices.rules
CALIB_FILE=/etc/yubi/encoder_limits.yaml
VARIANT="stationary"
if [ -n "$SW" ] && [ -f "$SW/.env" ]; then
  _v=$(grep -E '^ROBOT_VARIANT=' "$SW/.env" | tail -1 | cut -d= -f2- | cut -d'#' -f1 | tr -d ' "')
  [ -n "$_v" ] && VARIANT="$_v"
fi

calibration_state() {  # prints what's missing; exit 0 = everything in place
  local bad=0 l
  local links="/dev/yubi_left_camera /dev/yubi_right_camera /dev/yubi_left_esp32c6 /dev/yubi_right_esp32c6"
  [ "$VARIANT" = portable ] && links="$links /dev/yubi_center_camera"
  [ -f "$RULES_FILE" ] || { echo "udevルール未保存 ($RULES_FILE)"; bad=1; }
  if [ -f "$RULES_FILE" ] && ! head -1 "$RULES_FILE" | grep -q "variant=$VARIANT"; then
    echo "udevルールが別バリアントで保存されています（現在の設定: $VARIANT）"; bad=1
  fi
  [ -f "$CALIB_FILE" ] || { echo "エンコーダ原点未保存 ($CALIB_FILE)"; bad=1; }
  for l in $links; do [ -e "$l" ] || { echo "$l がありません（未接続 or USBポート変更）"; bad=1; }; done
  return $bad
}

run_device_setup() {
  local ds="" c
  for c in "$SW/deploy/yubi-device-setup.sh" "$REAL_HOME/Desktop/yubi-device-setup.sh"; do
    [ -f "$c" ] && { ds="$c"; break; }
  done
  [ -n "$ds" ] || { echo "!! yubi-device-setup.sh not found"; return 1; }
  echo "--- launching device setup: $ds (variant=$VARIANT)"
  # The GUI runs under sudo, so it needs a terminal for the password prompt.
  # --wait blocks until the operator closes the calibration terminal.
  if command -v gnome-terminal >/dev/null 2>&1 && [ -n "${DISPLAY:-}" ]; then
    gnome-terminal --wait -- bash -c "bash '$ds' --no-up" 2>/dev/null
  elif [ -t 0 ]; then
    bash "$ds" --no-up
  else
    notify-send -u critical "YUBI" "ターミナルを開けずセットアップGUIを起動できません" 2>/dev/null || true
    return 1
  fi
}

if [ "$DRYRUN" = 1 ]; then
  rc=0
  [ -n "$SW" ]        || { echo "!! FAIL: yubi-sw stack dir not found"; rc=1; }
  [ -n "$APP" ]       || { echo "!! WARN: yubi-app stack dir not found"; }
  [ -f "$QUEST_CFG" ] || { echo "!! WARN: quest config not found"; }
  if _missing=$(calibration_state); then
    echo "--- calibration: OK (variant=$VARIANT)"
  else
    echo "--- calibration: INCOMPLETE (variant=$VARIANT) - a real run would open the setup GUI"
    echo "$_missing" | sed 's/^/      /'
  fi
  echo "--- dry run: not touching docker / quest config / browser"
  [ "$rc" = 0 ] && echo "======== dry run OK ========" || echo "======== dry run FAILED ========"
  exit "$rc"
fi

# --- startup menu: normal start / change Quest IP + variant / recalibrate -----
# zenity exits 1 on Cancel and 5 on --timeout; both fall through to a normal
# start, so a double-click with nobody at the keyboard still boots the stack.
CHOICE="start"
if command -v zenity >/dev/null 2>&1; then
  _sel=$(zenity --list --radiolist --title="YUBI 起動" \
        --text="どうしますか？（15秒後に自動でそのまま起動します）" \
        --hide-header --timeout=15 --height=240 --width=520 \
        --column="" --column="操作" \
        TRUE "そのまま起動" \
        FALSE "Quest IP・構成タイプを変更して起動" \
        FALSE "デバイス再キャリブレーション（カメラ/エンコーダ）してから起動" \
        2>/dev/null) || true
  case "${_sel:-}" in
    *"Quest IP"*)   CHOICE="quest_ip" ;;
    *キャリブ*)      CHOICE="recalib" ;;
  esac
fi
echo "--- startup choice: $CHOICE"

# --- variant switch (stationary/portable), offered on the change-settings path.
# Writes the single source of truth ($SW/.env ROBOT_VARIANT) after backing it
# up. A change makes calibration_state below flag the variant-stamp mismatch,
# which walks the operator straight into recalibration - no extra wiring.
choose_variant() {
  command -v zenity >/dev/null 2>&1 || return 0
  if [ ! -f "$SW/.env" ]; then
    zenity --error --text="設定ファイル（.env）がまだありません。先に make install を実行してください。" 2>/dev/null || true
    return 0
  fi
  local st=FALSE po=FALSE sel new
  if [ "$VARIANT" = "portable" ]; then po=TRUE; else st=TRUE; fi
  sel=$(zenity --list --radiolist --title="構成タイプ" \
        --text="この機体の構成タイプ（現在: $VARIANT）" \
        --hide-header --height=200 --width=520 \
        --column="" --column="type" \
        "$st" "stationary（据え置き: やぐら＋RealSense頭＋フットペダル）" \
        "$po" "portable（装着型: USBカメラ3台＋Quest操作）" \
        2>/dev/null) || { echo "--- variant unchanged ($VARIANT)"; return 0; }
  case "$sel" in
    portable*)   new="portable" ;;
    stationary*) new="stationary" ;;
    *)           echo "--- variant unchanged ($VARIANT)"; return 0 ;;
  esac
  [ "$new" = "$VARIANT" ] && { echo "--- variant unchanged ($VARIANT)"; return 0; }
  local bak="$SW/.env.bak-$(date +%Y%m%d-%H%M%S)"
  cp -a "$SW/.env" "$bak" || { echo "!! could not back up .env - variant NOT changed"; return 0; }
  if grep -qE '^ROBOT_VARIANT=' "$SW/.env"; then
    sed -i -E "s/^ROBOT_VARIANT=.*/ROBOT_VARIANT=$new/" "$SW/.env"
  else
    echo "ROBOT_VARIANT=$new" >> "$SW/.env"
  fi
  if grep -qE "^ROBOT_VARIANT=$new$" "$SW/.env"; then
    echo "--- variant: $VARIANT -> $new (backup: $bak)"
    VARIANT="$new"
    notify-send -i video-display "YUBI" "構成タイプを $new に変更（元の設定は $(basename "$bak")）" 2>/dev/null || true
  else
    echo "!! .env edit did not take - restoring backup"
    cp -a "$bak" "$SW/.env"
  fi
}
if [ "$CHOICE" = "quest_ip" ]; then choose_variant; fi

NEED_SETUP=0
if [ "$CHOICE" = "recalib" ]; then
  NEED_SETUP=1
elif ! _missing=$(calibration_state); then
  echo "--- calibration incomplete:"
  echo "$_missing" | sed 's/^/      /'
  if command -v zenity >/dev/null 2>&1; then
    zenity --question --title="YUBI" --ok-label="セットアップを開く" --cancel-label="このまま起動" \
      --text="キャリブレーションが未保存か、デバイス構成が変わっています:\n\n$_missing\n\nセットアップGUIを開きますか？" \
      2>/dev/null && NEED_SETUP=1
  else
    NEED_SETUP=1
  fi
fi
if [ "$NEED_SETUP" = 1 ]; then
  run_device_setup
  if _missing=$(calibration_state); then
    echo "--- calibration now OK"
  else
    echo "!! calibration still incomplete - starting anyway:"
    echo "$_missing" | sed 's/^/      /'
    notify-send -u critical -i dialog-warning "YUBI" "キャリブレーション未完了のまま起動します" 2>/dev/null || true
  fi
fi

# --- Quest headset IP: the airoa_quest bridge connects to the Quest at the IP in
#     yubi_bringup/config/local/yubi_devices.yaml. It only changes when the wifi
#     changes. Verify it pings; if not, ask the operator for the IP shown on the
#     Quest's YUBI-app screen and update the config BEFORE the stack starts.
ensure_quest_ip() {  # ensure_quest_ip [force] - "force" opens the dialog even when reachable
  local force="${1:-}"
  [ -f "$QUEST_CFG" ] || { echo "!! quest config not found"; return 0; }
  local cur; cur=$(grep -oE 'quest_ip:[[:space:]]*"?([0-9]{1,3}\.){3}[0-9]{1,3}' "$QUEST_CFG" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1)
  echo "--- Quest IP: ${cur:-<unset>} ; pinging..."
  if [ -z "$force" ] && [ -n "$cur" ] && [ "$cur" != "0.0.0.0" ] && ping -c1 -W2 "$cur" >/dev/null 2>&1; then echo "    OK reachable"; return 0; fi
  if [ -z "$force" ]; then
    echo "    Quest NOT reachable at ${cur:-<unset>} -- asking operator via dialog"
    notify-send -u critical -i dialog-warning "YUBI" "Questに接続できません。IPを入力してください" 2>/dev/null || true
  fi
  local new=""
  while true; do
    new=$(zenity --entry --title="Quest IP" --text="QuestのYUBIアプリ画面のIPを入力（例: 192.168.11.5）" --entry-text="${cur}" 2>/dev/null) || { echo "    cancelled - keeping ${cur:-<unset>}"; return 0; }
    echo "$new" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$' && break
    zenity --error --text="IP形式が不正: $new" 2>/dev/null || true
  done
  sed -i -E "s#(quest_ip:[[:space:]]*\")[^\"]*(\")#\1${new}\2#" "$QUEST_CFG"; echo "    quest_ip -> $new"
  if ping -c1 -W2 "$new" >/dev/null 2>&1; then
    notify-send -i video-display "YUBI" "Quest IPを $new に更新（到達OK）" 2>/dev/null || true
  else
    notify-send -u critical -i dialog-warning "YUBI" "Quest IPを $new に更新（まだ到達せず。Quest/Wi-Fiを確認）" 2>/dev/null || true
  fi
}
# --- disk space: last line of defense (yubi-disk-guard.sh cleans from cron) ---
FREE_GB=$(df -BG --output=avail "$REAL_HOME" 2>/dev/null | tail -1 | tr -dc 0-9)
echo "--- free disk: ${FREE_GB:-?}GB"
if [ -n "$FREE_GB" ] && [ "$FREE_GB" -lt 30 ]; then
  echo "!! low disk (${FREE_GB}GB free)"
  if command -v zenity >/dev/null 2>&1; then
    zenity --question --default-cancel --title="YUBI" \
      --ok-label="それでも起動" --cancel-label="中止" \
      --text="ディスク残量が${FREE_GB}GBしかありません。\nこのまま録画するとPCが停止する恐れがあります。\n（自動掃除が数分内に走ります — 少し待つか、管理者に連絡してください）\n\nそれでも起動しますか？" 2>/dev/null \
      || { echo "    operator aborted (low disk)"; exit 0; }
  fi
fi

if [ "$CHOICE" = "quest_ip" ]; then ensure_quest_ip force; else ensure_quest_ip; fi

# --- don't yank a recording out from under the operator ------------------------
# Starting while the stack is already up is a forced down+up (that IS the
# intended repair action for a wedged stack) — but if an episode is being
# recorded right now, the restart would kill it, so ask first.
if docker top yubi_core 2>/dev/null | grep -q rosbag2 || docker top yubi 2>/dev/null | grep -q rosbag2; then
  echo "--- a recording appears to be in progress"
  if command -v zenity >/dev/null 2>&1; then
    if ! zenity --question --default-cancel --title="YUBI" \
         --ok-label="強制再起動する" --cancel-label="中止（録画を続ける）" \
         --text="録画が進行中のようです。\n再起動すると進行中のエピソードは失われます。\n\n強制再起動しますか？" 2>/dev/null; then
      echo "    operator cancelled - leaving the running stack untouched"
      notify-send -i video-display "YUBI" "起動を中止しました（録画継続中）" 2>/dev/null || true
      exit 0
    fi
  fi
  echo "    operator confirmed the forced restart"
fi

restart_stack "$SW"
restart_stack "$APP"

# --- verify LAN link to 6000pro (the data-sync target) ---
echo "--- checking 6000pro LAN sync link (10.10.10.2) ..."
if ping -c1 -W2 10.10.10.2 >/dev/null 2>&1; then
  echo "    OK 6000pro reachable - recorded episodes will auto-sync over LAN"
else
  echo "    WARNING: 6000pro NOT reachable on 10.10.10.2 - data stays local until fixed."
  notify-send -u critical -i dialog-warning "YUBI" "⚠ 6000proに接続できません。LANケーブルを確認してください" 2>/dev/null || true
  echo "      Check the LAN cable, or run:  sudo nmcli con up omakase-lan"
fi

echo "--- waiting for http://localhost:3000/web ..."
up=0; for i in $(seq 1 90); do wait_for_url "http://localhost:3000/web" && { echo "    web up after ${i}s"; up=1; break; }; sleep 1; done
[ "$up" = 0 ] && echo "!! web did not answer in 90s — opening anyway"
open_url "http://localhost:3000/web"
sleep 1
open_url "http://localhost:3000/web/dashboard"
notify-send -i video-display "YUBI" "✅ 起動完了。録画UIを開きました" 2>/dev/null || true
echo "======== done ========"; sleep 2
