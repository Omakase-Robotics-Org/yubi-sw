#!/usr/bin/env bash
# start-yubi.sh — one-click YUBI startup (works on any yubi box; auto-detects the
# yubi-sw / yubi-app stack dirs so nested (~/projects/yubi-sw/yubi-sw) and flat
# (~/projects/yubi-sw) layouts both work).
#   1. refuses to run twice at once (single-instance flock guard)
#   2. asks the Quest IP if the headset isn't reachable
#   3. (re)starts yubi-sw + yubi-app docker stacks
#   4. checks the 6000pro LAN sync link
#   5. waits until the web app answers on :3000
#   6. opens the recording UI + the dashboard (2 browser windows)
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

if [ "$DRYRUN" = 1 ]; then
  rc=0
  [ -n "$SW" ]        || { echo "!! FAIL: yubi-sw stack dir not found"; rc=1; }
  [ -n "$APP" ]       || { echo "!! WARN: yubi-app stack dir not found"; }
  [ -f "$QUEST_CFG" ] || { echo "!! WARN: quest config not found"; }
  echo "--- dry run: not touching docker / quest config / browser"
  [ "$rc" = 0 ] && echo "======== dry run OK ========" || echo "======== dry run FAILED ========"
  exit "$rc"
fi

# --- Quest headset IP: the airoa_quest bridge connects to the Quest at the IP in
#     yubi_bringup/config/local/yubi_devices.yaml. It only changes when the wifi
#     changes. Verify it pings; if not, ask the operator for the IP shown on the
#     Quest's YUBI-app screen and update the config BEFORE the stack starts.
ensure_quest_ip() {
  [ -f "$QUEST_CFG" ] || { echo "!! quest config not found"; return 0; }
  local cur; cur=$(grep -oE 'quest_ip:[[:space:]]*"?([0-9]{1,3}\.){3}[0-9]{1,3}' "$QUEST_CFG" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1)
  echo "--- Quest IP: ${cur:-<unset>} ; pinging..."
  if [ -n "$cur" ] && [ "$cur" != "0.0.0.0" ] && ping -c1 -W2 "$cur" >/dev/null 2>&1; then echo "    OK reachable"; return 0; fi
  echo "    Quest NOT reachable at ${cur:-<unset>} -- asking operator via dialog"
  notify-send -u critical -i dialog-warning "YUBI" "Questに接続できません。IPを入力してください" 2>/dev/null || true
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
ensure_quest_ip
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
