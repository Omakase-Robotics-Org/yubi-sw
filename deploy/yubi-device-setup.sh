#!/usr/bin/env bash
# yubi-device-setup.sh — one-click camera/encoder calibration for any yubi box.
#
# Runs tools/yubi_udev_setup.sh (udev rules + encoder origins) with the variant
# taken from the stack's .env, after hard-resetting the gripper ESP32C6 boards
# (their USB-CDC interface wedges after a re-plug and streams nothing until
# reset). The docker stack is stopped for the calibration and ALWAYS brought
# back up on exit, success or failure.
#
# CANONICAL COPY: deploy/yubi-device-setup.sh in Omakase-Robotics-Org/yubi-sw.
# Install with deploy/install-launcher.sh — do not hand-edit the Desktop copy.
# (yubi2's hand-made Desktop copy hardcoded "--variant portable" and kept
# failing with exit=3 after the box switched to the stationary profile on
# 2026-07-29 — the variant must follow the stack config, never the script.)
#
# Usage:  yubi-device-setup.sh [--variant stationary|portable] [--no-up]
#   --variant  override (default: ROBOT_VARIANT from the stack's .env)
#   --no-up    leave the stack down on exit (start-yubi.sh passes this,
#              because it restarts the stacks itself right after)
# Needs a terminal: the calibration GUI is launched under sudo.
set -uo pipefail

VARIANT_OVERRIDE=""
NO_UP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --variant) VARIANT_OVERRIDE="${2:-}"; shift 2 ;;
    --variant=*) VARIANT_OVERRIDE="${1#--variant=}"; shift ;;
    --no-up) NO_UP=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "!! unknown argument: $1"; exit 2 ;;
  esac
done

# Resolve the operator's home ($HOME is root's when invoked under sudo).
REAL_HOME="$HOME"
if [ -n "${SUDO_USER:-}" ]; then
  _h="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6)"
  [ -n "$_h" ] && REAL_HOME="$_h"
fi
LOG="$REAL_HOME/yubi-device-setup.log"
exec > >(tee -a "$LOG") 2>&1

pause_and_exit() {  # keep the terminal window open so the operator reads the result
  local rc="$1"
  [ -t 0 ] && read -rp "Enter で閉じる" || true
  exit "$rc"
}

dc() { if docker compose version >/dev/null 2>&1; then docker compose "$@"; else docker-compose "$@"; fi; }
find_stack() { local d f; for d in "$@"; do for f in docker-compose.yml docker-compose.yaml compose.yaml compose.yml; do
  [ -f "$d/$f" ] && { echo "$d"; return 0; }; done; done; return 1; }

SW=$(find_stack \
  "$REAL_HOME/projects/yubi-sw" \
  "$REAL_HOME/projects/yubi-sw/yubi-sw" \
  "$REAL_HOME/Desktop/yubi-sw" \
  "$REAL_HOME/Desktop/yubi-sw-new")
if [ -z "$SW" ] || [ ! -f "$SW/tools/yubi_udev_setup.sh" ]; then
  echo "!! yubi-sw checkout not found (looked under ~/projects and ~/Desktop)"
  notify-send -u critical "YUBI" "yubi-swリポジトリが見つかりません" 2>/dev/null || true
  pause_and_exit 1
fi

# Variant: CLI override > stack .env > stationary. Never hardcoded here.
VARIANT="$VARIANT_OVERRIDE"
if [ -z "$VARIANT" ] && [ -f "$SW/.env" ]; then
  VARIANT=$(grep -E '^ROBOT_VARIANT=' "$SW/.env" | tail -1 | cut -d= -f2- | cut -d'#' -f1 | tr -d ' "')
fi
VARIANT="${VARIANT:-stationary}"

echo ""; echo "======== $(date '+%F %T') :: YUBI device setup (variant=$VARIANT, repo=$SW) ========"

# sudo needs a way to ask for the password: a TTY, or a cached timestamp.
if [ ! -t 0 ] && ! sudo -n true 2>/dev/null; then
  echo "!! no terminal for the sudo password prompt - run this from a terminal"
  notify-send -u critical "YUBI" "ターミナルから実行してください（sudoパスワードが必要）" 2>/dev/null || true
  exit 1
fi

# Bring the stack back up no matter how the calibration ends: a run that died
# mid-way on 2026-08-17 left yubi2's recording stack stopped until someone
# noticed. start-yubi.sh passes --no-up and does its own restart instead.
CLEANED=0
cleanup() {
  [ "$CLEANED" = 1 ] && return 0
  CLEANED=1
  if [ "$NO_UP" = 1 ]; then
    echo "--- (--no-up) スタックは呼び出し元が再起動します"
    return 0
  fi
  echo "--- スタックを再起動します (docker compose up -d)"
  ( cd "$SW" && dc up -d --force-recreate yubi && dc up -d ) \
    || echo "!! stack restart failed - run: cd $SW && docker compose up -d"
}
trap cleanup EXIT

echo "--- 収録スタックを停止します（キャリブレーション中はカメラ/シリアルを解放）"
( cd "$SW" && dc stop yubi ) >/dev/null 2>&1 || true

echo "--- グリッパESP32C6をハードリセット（USB-CDCウェッジ対策。抜き差し後は必須）"
ESPTOOL=""
for c in "$REAL_HOME/.local/bin/esptool" "$REAL_HOME/.local/bin/esptool.py" esptool esptool.py; do
  command -v "$c" >/dev/null 2>&1 && { ESPTOOL="$c"; break; }
done
if [ -z "$ESPTOOL" ]; then
  echo "    WARN: esptool not found - skipping the hard reset (encoders may stay silent)"
else
  for p in /dev/ttyACM*; do
    [ -e "$p" ] || continue
    # Only touch the ESP32C6 boards (VID 303a) - not the footpedal or anything else.
    udevadm info -q property -n "$p" 2>/dev/null | grep -q '^ID_VENDOR_ID=303a' || continue
    echo "    reset $p"
    "$ESPTOOL" --port "$p" --before default-reset --after hard-reset flash-id >/dev/null 2>&1 \
      || "$ESPTOOL" --port "$p" --before default_reset --after hard_reset flash_id >/dev/null 2>&1 \
      || echo "    WARN: esptool reset failed for $p"
  done
fi

echo "--- キャリブレーションGUIを起動します（sudoパスワードを聞かれたら入力）"
( cd "$SW/tools" && sudo -E bash yubi_udev_setup.sh --variant "$VARIANT" )
rc=$?

cleanup
if [ "$rc" -eq 0 ]; then
  echo "✅ キャリブレーション完了 (variant=$VARIANT)"
  ls -l /dev/yubi_* 2>/dev/null || true
  notify-send -i video-display "YUBI" "✅ デバイス設定完了 (variant=$VARIANT)" 2>/dev/null || true
else
  echo "!! キャリブレーションが異常終了しました (exit=$rc)。ログ: $LOG"
  notify-send -u critical "YUBI" "デバイス設定が失敗しました (exit=$rc)" 2>/dev/null || true
fi
pause_and_exit "$rc"
