# yubi2

Stationary profile (`ROBOT_VARIANT=stationary`, switched 2026-07-29), flat checkout at `~/projects/yubi-sw`,
remote `origin`. The launcher is the canonical one — see `deploy/SETUP.md` §4
and `deploy/install-launcher.sh`.

## Recording provenance (2026-07-29)

yubi2's recordings land under:

```
org=omakase-robotics/site=tokyo/location=meguro/…/robot_type=yubi/robot_id=6c4bef0b-…
```

Set the same way as yubi1 — org and site pinned in `config/local/robot_config.yaml`,
location owned by the backend:

```yaml
    runner_organization: "omakase-robotics"
    site: "tokyo"
    # location deliberately unpinned; the robot is assigned to the "Meguro"
    # location row and resolves to location=meguro
```

`config/local/robot_config.yaml` is **not** snapshotted here — it holds the
backend API key. After a reimage those two lines have to be re-added by hand,
along with `api_key` and `base_url: http://localhost:8000/api`.

The backend rows were renamed to agree with the pins, so the config and backend
layers cannot drift: organization `Omakase Robotics`, site `Tokyo`, location
`Meguro` (the robot is assigned to it). Renaming an organization needs the
`org:update` grant and the field is `display_name`, not `name`.

**Prefix boundary.** yubi2's 156 existing local objects (and its S3 copies) stay
at `org=sample-organization/site=unknown/location=sample-location`. Everything
recorded after the `yubi_core` restart on 2026-07-29 uses the key above. Old
data is deliberately left as-is.

## Files

- `config-local/yubi_devices.yaml` — snapshot of the per-host device overrides
  (gitignored on the box). Quest IP, RealSense head camera.

**yubi2 has no foot pedal**, so its per-host layer must supply everything the
stationary overlay does not: the `gripper_double_click_node` /
`portable_joy_command_node` presence markers, the `joy_source_topic` pin, and
the 60 Hz hand-camera pins (`common/` asks for 120). Those are in
`config-local/yubi_devices.yaml`.

**Known wart:** `config/stationary/` declares `footpedal_node`, and a per-host
layer can currently only turn a node *on*, not off. yubi2 therefore respawn-
loops `footpedal_node` (~29 restarts/min, `Cannot find footswitch device`).
Collection is unaffected — the glove path is verified working — but it is
constant log noise until a node-disable mechanism lands and the image is
rebuilt.
