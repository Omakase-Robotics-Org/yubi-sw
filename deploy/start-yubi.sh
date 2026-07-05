#!/usr/bin/env bash
# start-yubi.sh — one-click YUBI startup (works on any yubi box; auto-detects the
# yubi-sw / yubi-app stack dirs so nested (~/projects/yubi-sw/yubi-sw) and flat
# (~/projects/yubi-sw) layouts both work).
#   1. (re)starts yubi-sw + yubi-app docker stacks
#   2. asks the Quest IP if the headset isn't reachable
#   3. waits until the web app answers on :3000
#   4. opens the recording UI + the dashboard (2 browser windows)
# Copy to ~/Desktop/start-yubi.sh (+ Start-YUBI.desktop) and double-click it.
set -uo pipefail
LOG="$HOME/yubi-start.log"; exec > >(tee -a "$LOG") 2>&1
echo ""; echo "======== $(date '+%F %T') :: starting YUBI ========"
notify-send -i video-display "YUBI" "起動中… 数十秒お待ちください" 2>/dev/null || true

dc() { if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi; }
find_stack() { local d f; for d in "$@"; do for f in docker-compose.yml docker-compose.yaml compose.yaml compose.yml; do
  [ -f "$d/$f" ] && { echo "$d"; return 0; }; done; done; return 1; }
restart_stack() { local dir="$1"; [ -n "$dir" ] && [ -d "$dir" ] || { echo "!! stack dir not found"; return 1; }
  echo "--- restarting stack in $dir"; ( cd "$dir" && dc down; dc up -d ); }

SW=$(find_stack "$HOME/projects/yubi-sw" "$HOME/projects/yubi-sw/yubi-sw" "$HOME/Desktop/yubi-sw")
APP=$(find_stack "$HOME/projects/yubi-app" "$HOME/projects/yubi-app/yubi-app")
QUEST_CFG=$(find "$HOME/projects/yubi-sw" -path "*config/local/yubi_devices.yaml" 2>/dev/null | head -1)
echo "detected: yubi-sw=$SW  yubi-app=$APP  quest_cfg=$QUEST_CFG"

ensure_quest_ip() {
  [ -f "$QUEST_CFG" ] || { echo "!! quest config not found"; return 0; }
  local cur; cur=$(grep -oE 'quest_ip:[[:space:]]*"?([0-9]{1,3}\.){3}[0-9]{1,3}' "$QUEST_CFG" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -1)
  echo "--- Quest IP: ${cur:-<unset>} ; pinging..."
  if [ -n "$cur" ] && ping -c1 -W2 "$cur" >/dev/null 2>&1; then echo "    OK reachable"; return 0; fi
  notify-send -u critical -i dialog-warning "YUBI" "Questに接続できません。IPを入力してください" 2>/dev/null || true
  local new=""
  while true; do
    new=$(zenity --entry --title="Quest IP" --text="QuestのYUBIアプリ画面のIPを入力（例: 192.168.11.5）" --entry-text="${cur}" 2>/dev/null) || { echo "    cancelled"; return 0; }
    echo "$new" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$' && break
    zenity --error --text="IP形式が不正: $new" 2>/dev/null || true
  done
  sed -i -E "s#(quest_ip:[[:space:]]*\")([0-9.]+)(\")#\1${new}\3#" "$QUEST_CFG"; echo "    quest_ip -> $new"
}
ensure_quest_ip
restart_stack "$SW"
restart_stack "$APP"

echo "--- waiting for http://localhost:3000/web ..."
up=0; for i in $(seq 1 90); do curl -fsS -o /dev/null "http://localhost:3000/web" && { echo "    web up after ${i}s"; up=1; break; }; sleep 1; done
[ "$up" = 0 ] && echo "!! web did not answer in 90s — opening anyway"
xdg-open "http://localhost:3000/web"           >/dev/null 2>&1 &
sleep 1
xdg-open "http://localhost:3000/web/dashboard" >/dev/null 2>&1 &
notify-send -i video-display "YUBI" "✅ 起動完了。録画UIを開きました" 2>/dev/null || true
echo "======== done ========"; sleep 2
