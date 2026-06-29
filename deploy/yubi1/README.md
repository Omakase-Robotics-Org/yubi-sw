# yubi1 operator startup files

One-click startup assets for the **yubi1** data-collection workstation. They let the
on-site operator launch the whole YUBI stack with a single double-click — no terminal.

## Files

- `start-yubi.sh` — the one-click startup script. It:
  1. (re)starts the `yubi-sw` and `yubi-app` docker compose stacks;
  2. checks the Quest headset IP from the bringup config and, if it doesn't ping,
     prompts the operator via a `zenity` dialog to enter the IP shown on the Quest's
     YUBI-app screen, then rewrites the config in place;
  3. checks the 6000pro LAN sync link (`10.10.10.2`) and warns if the data-sync
     target is unreachable;
  4. waits for the web app on `:3000` and opens the recording UI + dashboard.
  All progress is mirrored to `~/yubi-start.log` and surfaced as desktop
  notifications (`notify-send`).
- `Start-YUBI.desktop` — the GNOME launcher that runs `start-yubi.sh`.

## Canonical deployment

The canonical copies live on **yubi1** at:

- `~/Desktop/start-yubi.sh`
- `~/Desktop/Start-YUBI.desktop`

The copies in this repo are the version-controlled source of truth. When you change
them here, redeploy to `~/Desktop/` on yubi1.

## GNOME requirements

For GNOME to treat the launcher as a trusted, double-clickable app icon (rather than
showing it as a text file or a "Untrusted application launcher" warning), after
copying the files to `~/Desktop/` you must:

```sh
chmod +x ~/Desktop/start-yubi.sh
chmod +x ~/Desktop/Start-YUBI.desktop
gio set ~/Desktop/Start-YUBI.desktop metadata::trusted true
```

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
