#!/usr/bin/env python3
"""Interactive helper to map footpedal_states button indices to physical pedals.

Subscribes to /footpedal_states (sensor_msgs/Joy, 3 buttons) and prints a line
each time a button transitions 0 -> 1, so an operator can press pedals one at
a time and read off which index lit up.

Usage:
    ros2 run yubi_bringup identify_footpedal

Then press the pedals one at a time (e.g. right, center, left) and read the
printed button index for each. Compare against task_command_dispatch_node's
joy_accept_button / joy_reject_button / joy_cancel_episode_button in
config/portable/yubi_devices.yaml to confirm (or re-map) which physical pedal
triggers accept/reject/cancel.
"""

import rclpy
from rclpy.node import Node

from sensor_msgs.msg import Joy


class FootpedalIdentifyNode(Node):
    def __init__(self):
        super().__init__("footpedal_identify_node")

        self.declare_parameter("footpedal_topic", "/footpedal_states")
        self._prev_buttons = []

        self._subscription = self.create_subscription(
            Joy,
            self.get_parameter("footpedal_topic").value,
            self._joy_callback,
            10,
        )

        self.get_logger().info(
            "Listening on %s — press each pedal one at a time."
            % self.get_parameter("footpedal_topic").value
        )

    def _joy_callback(self, msg: Joy) -> None:
        buttons = list(msg.buttons)
        if not self._prev_buttons:
            self._prev_buttons = [0] * len(buttons)

        for index, (prev, current) in enumerate(zip(self._prev_buttons, buttons)):
            if prev == 0 and current == 1:
                print(f"button[{index}] pressed")

        self._prev_buttons = buttons


def main(args=None):
    rclpy.init(args=args)
    node = FootpedalIdentifyNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
