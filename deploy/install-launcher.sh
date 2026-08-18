#!/usr/bin/env bash
# install-launcher.sh — install the canonical YUBI launchers on this box.
#
# The launchers used to live only on each box's Desktop, which meant they died
# with a reimage and drifted between boxes (as of 2026-07-29 yubi1/yubi2/yubi3
# each ran a different start launcher, and yubi2's hand-made device-setup copy
# kept a stale hardcoded variant that failed every calibration after the
# 2026-07-29 profile switch). The repo copies are the single source of truth;
# this script pushes Start-YUBI and YUBI-Device-Setup onto the Desktop, into
# the application menu, and pins Start-YUBI to the GNOME dock.
#
# Usage:  ./deploy/install-launcher.sh
# Idempotent. Existing Desktop copies are backed up, never deleted.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/start-yubi.sh"
DESK_SRC="$HERE/Start-YUBI.desktop"
DS_SRC="$HERE/yubi-device-setup.sh"
DS_DESK_SRC="$HERE/YUBI-Device-Setup.desktop"
[ -f "$SRC" ] || { echo "!! $SRC not found"; exit 1; }
[ -f "$DESK_SRC" ] || { echo "!! $DESK_SRC not found"; exit 1; }
[ -f "$DS_SRC" ] || { echo "!! $DS_SRC not found"; exit 1; }
[ -f "$DS_DESK_SRC" ] || { echo "!! $DS_DESK_SRC not found"; exit 1; }

REAL_HOME="$(getent passwd "${SUDO_USER:-$USER}" | cut -d: -f6)"
[ -n "$REAL_HOME" ] || REAL_HOME="$HOME"
STAMP="$(date +%Y%m%d-%H%M%S)"
DESKTOP_DIR="$REAL_HOME/Desktop"
APPS_DIR="$REAL_HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR" "$APPS_DIR"

backup() {  # backup <path> — move aside, never delete
  local f="$1"
  [ -e "$f" ] || return 0
  if cmp -s "$f" "$2" 2>/dev/null; then echo "    unchanged: $f"; return 0; fi
  cp -a "$f" "$f.bak-$STAMP"
  echo "    backed up: $f -> $f.bak-$STAMP"
}

echo "--- installing launcher for $REAL_HOME"

backup "$DESKTOP_DIR/start-yubi.sh" "$SRC"
install -m 0755 "$SRC" "$DESKTOP_DIR/start-yubi.sh"
echo "    installed: $DESKTOP_DIR/start-yubi.sh"

# .desktop entries. Two copies are needed: the Desktop one (double-click) and the
# ~/.local/share/applications one (application menu + GNOME dock favourite).
# Keep the filename in applications/ lowercase-stable: the dock favourite is
# stored by filename, so renaming it silently unpins the icon.
for target in "$DESKTOP_DIR/Start-YUBI.desktop" "$APPS_DIR/start-yubi.desktop"; do
  backup "$target" "$DESK_SRC"
  install -m 0755 "$DESK_SRC" "$target"
  echo "    installed: $target"
done

backup "$DESKTOP_DIR/yubi-device-setup.sh" "$DS_SRC"
install -m 0755 "$DS_SRC" "$DESKTOP_DIR/yubi-device-setup.sh"
echo "    installed: $DESKTOP_DIR/yubi-device-setup.sh"
for target in "$DESKTOP_DIR/YUBI-Device-Setup.desktop" "$APPS_DIR/yubi-device-setup.desktop"; do
  backup "$target" "$DS_DESK_SRC"
  install -m 0755 "$DS_DESK_SRC" "$target"
  echo "    installed: $target"
done

# Mark the Desktop launchers trusted so GNOME does not show "Untrusted
# application launcher" and refuse to run them on double-click.
if command -v gio >/dev/null 2>&1; then
  for target in "$DESKTOP_DIR/Start-YUBI.desktop" "$DESKTOP_DIR/YUBI-Device-Setup.desktop"; do
    gio set "$target" metadata::trusted true 2>/dev/null \
      && echo "    marked trusted (gio): $target" || true
  done
fi
update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true

# Disk guard: ~/.local/bin + a cron entry (idempotent). This is what keeps a
# runaway/abandoned recording or docker debris from filling the disk and
# crashing the box — it must run without any operator involvement.
DG_SRC="$HERE/yubi-disk-guard.sh"
if [ -f "$DG_SRC" ]; then
  BIN_DIR="$REAL_HOME/.local/bin"
  mkdir -p "$BIN_DIR"
  backup "$BIN_DIR/yubi-disk-guard.sh" "$DG_SRC"
  install -m 0755 "$DG_SRC" "$BIN_DIR/yubi-disk-guard.sh"
  echo "    installed: $BIN_DIR/yubi-disk-guard.sh"
  CRON_LINE="*/10 * * * * $BIN_DIR/yubi-disk-guard.sh >/dev/null 2>&1"
  if ! crontab -l 2>/dev/null | grep -Fq "yubi-disk-guard.sh"; then
    ( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab - \
      && echo "    cron installed: $CRON_LINE" || echo "    !! could not install cron"
  else
    echo "    cron already present (yubi-disk-guard.sh)"
  fi
fi

# Pin to the GNOME dock if it is not already there.
if command -v gsettings >/dev/null 2>&1; then
  cur="$(gsettings get org.gnome.shell favorite-apps 2>/dev/null || echo '')"
  if [ -n "$cur" ] && ! echo "$cur" | grep -q "'start-yubi.desktop'"; then
    new="$(echo "$cur" | sed "s/]$/, 'start-yubi.desktop']/")"
    gsettings set org.gnome.shell favorite-apps "$new" 2>/dev/null \
      && echo "    pinned to GNOME dock" || echo "    !! could not pin (no session bus?)"
  else
    echo "    already pinned to GNOME dock (or gsettings unavailable)"
  fi
fi

echo "--- done. Backups (if any) are alongside the originals as *.bak-$STAMP"
echo "    undo:  for f in $DESKTOP_DIR/*.bak-$STAMP $APPS_DIR/*.bak-$STAMP; do [ -e \"\$f\" ] && mv \"\$f\" \"\${f%.bak-$STAMP}\"; done"
