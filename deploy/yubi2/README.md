# yubi2

Portable unit (`ROBOT_VARIANT=portable`), flat checkout at `~/projects/yubi-sw`,
remote `origin`. The launcher is the canonical one — see `deploy/SETUP.md` §4
and `deploy/install-launcher.sh`.

## Recording provenance (2026-07-29)

yubi2's recordings land under:

```
org=omakase-robotics/site=meguro/location=meguro/…/robot_type=yubi_portable/robot_id=6c4bef0b-…
```

Set the same way as yubi1 — org and site pinned in `config/local/robot_config.yaml`,
location owned by the backend:

```yaml
    runner_organization: "omakase-robotics"
    site: "meguro"
    # location deliberately unpinned; the robot is assigned to the "Meguro"
    # location row and resolves to location=meguro
```

`config/local/robot_config.yaml` is **not** snapshotted here — it holds the
backend API key. After a reimage those two lines have to be re-added by hand,
along with `api_key` and `base_url: http://localhost:8000/api`.

The backend rows were renamed to agree with the pins, so the config and backend
layers cannot drift: organization `Omakase Robotics`, site `Meguro`, location
`Meguro` (the robot is assigned to it). Renaming an organization needs the
`org:update` grant and the field is `display_name`, not `name`.

**Prefix boundary.** yubi2's 156 existing local objects (and its S3 copies) stay
at `org=sample-organization/site=unknown/location=sample-location`. Everything
recorded after the `yubi_core` restart on 2026-07-29 uses the key above. Old
data is deliberately left as-is.

## Files

- `config-local/yubi_devices.yaml` — snapshot of the per-host device overrides
  (gitignored on the box). Quest IP, RealSense head camera.

No task-input pin is needed: yubi2 is portable, and
`config/portable/yubi_devices.yaml` already selects `/portable_joy_command`.
