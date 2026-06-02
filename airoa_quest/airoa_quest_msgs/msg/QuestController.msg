# Header.stamp = ROS clock at receive on the PC. Identical to the matching
# /quest/hmd/state and the other controller's message from the same source frame.
std_msgs/Header header

# Quest-device monotonic clock (nanoseconds) at sample generation.
# Current protocol (TCP/JSON): ovrTimeNs (OVRPlugin runtime clock).
# Future protocol (UDP/binary, yubi_quest_app): Android
# SystemClock.elapsedRealtimeNanos.
uint64 device_time_ns

# PC monotonic clock (nanoseconds) at receive (time.monotonic_ns()). See
# QuestHmd.msg for the post-processing alignment formula.
uint64 pc_monotonic_ns

# Packet sequence number from the Quest. 0 if the protocol does not provide
# one (current TCP/JSON has no seq).
uint32 seq

# Quest device identifier (UUID-style hex string). Empty under the current
# protocol.
string quest_id

# Controller pose expressed in the Quest tracking-space origin
# (OVRCameraRig.trackingSpace on the device side; published as TF frame
# `quest_origin` on the ROS side, name configurable via the bridge's
# `parent_frame` parameter).
# Coordinate convention: ROS (X forward, Y left, Z up, right-handed). The
# bridge converts from Unity (X right, Y up, Z forward, left-handed) at the
# boundary. This matches the parent of /tf frames `quest_left_controller` /
# `quest_right_controller`, so a consumer can use either this message or the
# TF tree without re-mapping coordinates.
# Note: this is the *raw OVR controller anchor* pose, not the offset-corrected
# `*_hand_root` frame computed downstream by the bridge for IK consumers.
geometry_msgs/Vector3 position
geometry_msgs/Quaternion rotation

# OVR tracking flags reported by the device for this controller.
bool tracked
bool valid

# Button / trigger / grip / joystick inputs are published separately as
# sensor_msgs/Joy on /quest/controller/{left,right}/joy (see quest_bridge_node).
# Battery state is published separately as sensor_msgs/BatteryState on
# /quest/controller/{left,right}/battery.
