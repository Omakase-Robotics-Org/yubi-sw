#!/usr/bin/env bash
#
# start-yubi.sh — one-click YUBI startup
#   1. (re)starts yubi-sw and yubi-app docker stacks
#   2. waits until the web app answers on :3000
#   3. opens the recording UI and the dashboard in the browser
#
# Put this on the Desktop and double-click it (or run it from a terminal).

set -uo pipefail

LOG="$HOME/yubi-start.log"
exec > >(tee -a "$LOG") 2>&1
echo ""
echo "================ $(date '+%Y-%m-%d %H:%M:%S') :: starting YUBI ================"
notify-send -i video-display "YUBI" "起動中… 数十秒お待ちください" 2>/dev/null || true

# Use docker compose v2 if present, otherwise fall back to docker-compose v1.
dc() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

restart_stack() {
  local dir="$1"
  echo "--- restarting stack in $dir"
  if [ ! -d "$dir" ]; then
    echo "!! directory not found: $dir" >&2
    return 1
  fi
  ( cd "$dir" && dc down ; dc up -d )
}

# --- Quest headset IP: the airoa_quest bridge connects to the Quest at the IP
#     in yubi_bringup/config/local/yubi_devices.yaml. It only changes when the
#     wifi changes. Verify it pings; if not, ask the operator for the IP shown on
#     the Quest's YUBI-app screen and update the config BEFORE the stack starts.
QUEST_CFG="$HOME/projects/yubi-sw/yubi-sw/yubi_bringup/config/local/yubi_devices.yaml"
ensure_quest_ip() {
  [ -f "$QUEST_CFG" ] || { echo "!! quest config not found: $QUEST_CFG"; return 0; }
  local cur
  cur=$(grep -oE 'quest_ip:[[:space:]]*"?([0-9]{1,3}\.){3}[0-9]{1,3}' "$QUEST_CFG" \
        | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1)
  echo "--- Quest IP configured: ${cur:-<unset>} ; pinging..."
  if [ -n "$cur" ] && ping -c1 -W2 "$cur" >/dev/null 2>&1; then
    echo "    OK Quest reachable at $cur"; return 0
  fi
  echo "    Quest NOT reachable at ${cur:-<unset>} -- asking operator via dialog"
  notify-send -u critical -i dialog-warning "YUBI" "Questに接続できません。IPを入力してください" 2>/dev/null || true
  local new=""
  while true; do
    new=$(zenity --entry --title="Quest IP" \
      --text="QuestのYUBIアプリ画面に表示されているIPアドレスを入力してください（例: 192.168.11.5）" \
      --entry-text="${cur}" 2>/dev/null) || { echo "    operator cancelled - keeping ${cur:-<unset>}"; return 0; }
    if echo "$new" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then break; fi
    zenity --error --text="IPアドレスの形式が不正です: $new" 2>/dev/null || true
  done
  sed -i -E "s#(quest_ip:[[:space:]]*\")([0-9.]+)(\")#\1${new}\3#" "$QUEST_CFG"
  echo "    quest_ip updated -> $new"
  if ping -c1 -W2 "$new" >/dev/null 2>&1; then
    notify-send -i video-display "YUBI" "Quest IPを $new に更新（到達OK）" 2>/dev/null || true
  else
    notify-send -u critical -i dialog-warning "YUBI" "Quest IPを $new に更新（まだ到達せず。Quest/Wi-Fiを確認）" 2>/dev/null || true
  fi
}
ensure_quest_ip

restart_stack "$HOME/projects/yubi-sw/yubi-sw"
restart_stack "$HOME/projects/yubi-app/yubi-app"

# --- verify LAN link to 6000pro (the data-sync target) ---
echo "--- checking 6000pro LAN sync link (10.10.10.2) ..."
if ping -c1 -W2 10.10.10.2 >/dev/null 2>&1; then
  echo "    OK 6000pro reachable - recorded episodes will auto-sync over LAN"
else
  echo "    WARNING: 6000pro NOT reachable on 10.10.10.2 - data stays local until fixed."
  notify-send -u critical -i dialog-warning "YUBI" "⚠ 6000proに接続できません。LANケーブルを確認してください" 2>/dev/null || true
  echo "      Check the LAN cable, or run:  sudo nmcli con up omakase-lan"
fi

# Wait for the web app to come up (max 90s) before opening the browser.
echo "--- waiting for http://localhost:3000/web ..."
up=0
for i in $(seq 1 90); do
  if curl -fsS -o /dev/null "http://localhost:3000/web"; then
    echo "    web up after ${i}s"
    up=1
    break
  fi
  sleep 1
done
[ "$up" = 0 ] && echo "!! web did not answer within 90s — opening browser anyway"

# Open both pages. xdg-open uses the default browser.
xdg-open "http://localhost:3000/web"            >/dev/null 2>&1 &
sleep 1
xdg-open "http://localhost:3000/web/dashboard"  >/dev/null 2>&1 &

notify-send -i video-display "YUBI" "✅ 起動完了。録画UIを開きました" 2>/dev/null || true
echo "================ done ================"
sleep 2
