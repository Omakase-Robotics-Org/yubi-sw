#!/usr/bin/env bash
# yubi-disk-guard.sh — keep a yubi box from filling its disk and falling over.
#
# On 2026-08-13 a recording on yubi1 was never stopped and its single .mcap
# grew to 253GB over four days; the disk hit 95% and the box had crashed the
# same way before. Nothing watched disk space, nothing capped a recording, and
# nothing ever deleted local data that was already safe in AWS S3. This guard
# runs from cron (*/10) and handles all of that WITHOUT the operator:
#
#   1. orphan sweep  — recordings under $DATA/rosbags that stopped growing
#                      more than KEEP_HOURS ago were abandoned mid-flight
#                      (the normal record->MinIO flow removes its rosbag dir
#                      within minutes); delete them. Recent ones are kept as
#                      a recovery buffer.
#   2. runaway stop  — a .mcap still growing past RUNAWAY_GB can only be a
#                      recording nobody is going to stop (a real episode is
#                      well under 1GB): restart yubi_core to stop it, then
#                      delete the file.
#   3. thresholds    — free space below WARN_GB: warn the operator's desktop
#                      and reclaim docker debris (dangling volumes, build
#                      cache — 53GB of it on yubi1 by 2026-08-18). Below
#                      CRIT_GB: sweep harder (KEEP_HOURS -> CRIT_KEEP_HOURS)
#                      and alert loudly.
#
# Local MinIO episodes already uploaded to AWS S3 are garbage-collected by the
# uploader itself (yubi_s3_direct.py, YUBI_GC_DAYS) — not here — because only
# the uploader can verify an object really is in S3 before deleting it.
#
# CANONICAL COPY: deploy/yubi-disk-guard.sh in Omakase-Robotics-Org/yubi-sw.
# Installed to ~/.local/bin + cron by deploy/install-launcher.sh.
set -uo pipefail

KEEP_HOURS="${YUBI_GUARD_KEEP_HOURS:-48}"        # orphan recordings younger than this survive
CRIT_KEEP_HOURS="${YUBI_GUARD_CRIT_KEEP_HOURS:-6}"
RUNAWAY_GB="${YUBI_GUARD_RUNAWAY_GB:-25}"        # a growing mcap past this is stopped + deleted
WARN_GB="${YUBI_GUARD_WARN_GB:-80}"              # free-space warning + docker housekeeping
CRIT_GB="${YUBI_GUARD_CRIT_GB:-30}"              # free-space critical

REAL_HOME="$HOME"
if [ -n "${SUDO_USER:-}" ]; then
  _h="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6)"
  [ -n "$_h" ] && REAL_HOME="$_h"
fi
LOG="$REAL_HOME/yubi-disk-guard.log"
LOCK="$REAL_HOME/.yubi-disk-guard.lock"
exec 9>"$LOCK"; flock -n 9 || exit 0
# keep our own log from becoming the disk problem
[ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 5000000 ] && : > "$LOG"
exec >>"$LOG" 2>&1

# cron has no session env; wire up the operator's desktop for notify-send
export DISPLAY="${DISPLAY:-:0}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
notify() { notify-send -u "${1:-normal}" -i drive-harddisk "YUBI disk" "$2" 2>/dev/null || true; }

find_stack() { local d f; for d in "$@"; do for f in docker-compose.yml docker-compose.yaml; do
  [ -f "$d/$f" ] && { echo "$d"; return 0; }; done; done; return 1; }
SW=$(find_stack "$REAL_HOME/projects/yubi-sw" "$REAL_HOME/projects/yubi-sw/yubi-sw")

DATA="$REAL_HOME/yubi_data"
if [ -n "$SW" ] && [ -f "$SW/.env" ]; then
  _d=$(grep -E '^DATA_MOUNT_PATH=' "$SW/.env" | tail -1 | cut -d= -f2- | cut -d'#' -f1 | tr -d ' "')
  [ -n "$_d" ] && DATA=$(eval echo "$_d")   # expands ${HOME} from the .env value
fi
BAGS="$DATA/rosbags"

# The rosbag files are written by the container as root; delete through the
# container's own mount (host $DATA = /opt/data in yubi_core) instead of sudo.
rm_as_container() {  # rm_as_container <host-path-under-$DATA>
  local rel="${1#"$DATA"/}"
  docker exec yubi_core rm -rf "/opt/data/$rel" 2>/dev/null && return 0
  docker run --rm -v "$DATA:/opt/data" python:3.12-slim rm -rf "/opt/data/$rel" 2>/dev/null
}

echo "==== $(date '+%F %T') guard run ===="
_df_target="$DATA"; [ -d "$_df_target" ] || _df_target="$REAL_HOME"
FREE_GB=$(df -BG --output=avail "$_df_target" 2>/dev/null | tail -1 | tr -dc 0-9)
FREE_GB="${FREE_GB:-0}"
echo "free: ${FREE_GB}GB (warn<${WARN_GB} crit<${CRIT_GB})"

keep_hours="$KEEP_HOURS"
if [ "$FREE_GB" -lt "$CRIT_GB" ]; then
  keep_hours="$CRIT_KEEP_HOURS"
  notify critical "⚠ 残り${FREE_GB}GBしかありません。緊急掃除を実行中"
elif [ "$FREE_GB" -lt "$WARN_GB" ]; then
  notify normal "残り${FREE_GB}GB。自動掃除を実行します"
fi

# --- 1. orphan recordings ----------------------------------------------------
if [ -d "$BAGS" ]; then
  while IFS= read -r d; do
    [ -n "$d" ] || continue
    # skip if anything inside changed within the keep window (still in flight)
    if [ -n "$(find "$d" -newermt "-${keep_hours} hours" -print -quit 2>/dev/null)" ]; then
      continue
    fi
    sz=$(du -sh "$d" 2>/dev/null | cut -f1)
    echo "orphan: $d ($sz, idle >${keep_hours}h) - deleting"
    rm_as_container "$d" && echo "  deleted" || echo "  !! delete failed"
  done < <(find "$BAGS" -maxdepth 1 -mindepth 1 -type d -name '[0-9][0-9]-[0-9][0-9]-*' 2>/dev/null)
fi

# --- 2. runaway recording ----------------------------------------------------
# still growing (mtime within 10min) AND already huge -> nobody will stop it
if [ -d "$BAGS" ]; then
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    gb=$(( $(stat -c%s "$f" 2>/dev/null || echo 0) / 1000000000 ))
    [ "$gb" -ge "$RUNAWAY_GB" ] || continue
    echo "RUNAWAY: $f (${gb}GB, still growing) - restarting yubi_core and deleting"
    notify critical "⚠ 暴走録画(${gb}GB)を検知。録画を強制停止して削除します"
    docker restart yubi_core >/dev/null 2>&1
    sleep 5
    rm_as_container "$(dirname "$f")" && echo "  deleted" || echo "  !! delete failed"
  done < <(find "$BAGS" -type f -name '*.mcap' -newermt '-10 minutes' 2>/dev/null)
fi

# --- 3. docker debris (only when space is getting tight) ---------------------
if [ "$FREE_GB" -lt "$WARN_GB" ]; then
  docker volume prune -f 2>/dev/null | tail -1
  docker builder prune -f --keep-storage 2GB 2>/dev/null | tail -1
  # runaway logs (the uploader log was 27MB after 6 weeks; 200MB = something is wrong)
  for lg in "$REAL_HOME"/yubi_s3_direct.log "$REAL_HOME"/yubi-start.log; do
    [ -f "$lg" ] && [ "$(stat -c%s "$lg" 2>/dev/null || echo 0)" -gt 200000000 ] && : > "$lg" && echo "truncated $lg"
  done
fi

echo "done: $(df -BG --output=avail "$DATA" 2>/dev/null | tail -1 | tr -dc 0-9)GB free"
