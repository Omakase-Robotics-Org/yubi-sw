# Header.stamp = ROS clock at receive on the PC. Identical to the matching
# /quest/controller/left/state and /quest/controller/right/state messages from
# the same source frame, so cross-topic joining is trivial.
std_msgs/Header header

# Quest-device monotonic clock (nanoseconds) at sample generation.
# Current protocol (TCP/JSON): ovrTimeNs (OVRPlugin runtime clock).
# Future protocol (UDP/binary, yubi_quest_app): Android
# SystemClock.elapsedRealtimeNanos.
# Both are monotonic on the Quest and equivalent as the source-of-truth for
# offline alignment.
uint64 device_time_ns

# PC monotonic clock (nanoseconds) at receive (time.monotonic_ns()).
# Combined with device_time_ns over a sliding window in post-processing:
#   offset_ns = median(pc_monotonic_ns - device_time_ns)
#   aligned_pc_mono_ns = device_time_ns + offset_ns
uint64 pc_monotonic_ns

# Packet sequence number from the Quest. 0 if the protocol does not provide
# one (current TCP/JSON has no seq; future UDP has u32 seq, useful for loss
# detection in post-processing).
uint32 seq

# Quest device identifier (UUID-style hex string). Empty under the current
# protocol; the future protocol provides a 16-byte GUID.
string quest_id

# HMD pose expressed in the Quest tracking-space origin
# (OVRCameraRig.trackingSpace on the device side; published as TF frame
# `quest_origin` on the ROS side, name configurable via the bridge's
# `parent_frame` parameter).
# Coordinate convention: ROS (X forward, Y left, Z up, right-handed). The
# bridge converts from Unity (X right, Y up, Z forward, left-handed) at the
# boundary. This matches the parent of /tf frame `quest_hmd`, so a consumer
# can use either this message or the TF tree without re-mapping coordinates.
geometry_msgs/Vector3 position
geometry_msgs/Quaternion rotation
