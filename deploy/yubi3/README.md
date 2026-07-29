# yubi3

**Not yet in service — the rig/frame has not been built.** The box was
unreachable (`ssh yubi3` timed out) on 2026-07-29, so nothing here has been
applied live. Its missing RealSense, second USB camera and left gripper encoder
are not a fault; the hardware simply is not mounted yet.

## Before the first recording

yubi3 must not start collecting under placeholder provenance the way yubi1 and
yubi2 did — that is what put thousands of objects under `org=unknown` and
`site=unknown` in S3, and that old data is now deliberately being left as-is.

1. Copy the provenance template into place and fill in the API key:

   ```sh
   cp deploy/yubi3/config-local/robot_config.yaml \
      yubi_bringup/config/local/robot_config.yaml
   ```

2. Rename this box's backend rows to match the pins, so the config and backend
   layers agree (see the template for the exact endpoints): organization
   `Omakase Robotics`, site `Meguro`, location `Meguro` with the robot assigned
   to it.

3. Apply and verify — **before** recording anything:

   ```sh
   docker compose up config-init && docker compose restart yubi_core
   docker exec yubi_core bash -lc 'unset DISPLAY; \
     source /root/ros2_ws/install/setup.bash; \
     ros2 param get /record_manager site; \
     ros2 param get /record_manager runner_organization'
   ```

   Expect `meguro` and `omakase-robotics`. The key should read
   `org=omakase-robotics/site=meguro/location=meguro/…`.

`unset DISPLAY` matters: with a dead X display, `pactl`/ROS CLI calls block for
~130 s on a libpulse TCP timeout.

## Task input

yubi3's input path depends on its variant. Portable boxes get
`/portable_joy_command` from `config/portable/yubi_devices.yaml`. A stationary
box defaults to the footpedal; if yubi3 is stationary and its operators use the
gripper double-click instead, pin it in `config/local/yubi_devices.yaml` the way
`deploy/yubi1/config-local/yubi_devices.yaml` does.
