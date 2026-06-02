# Base image is built from yubi-core (see ./yubi-core). `make docker` and
# `docker compose build` produce it as yubi-core:latest before this image.
ARG YUBI_CORE_IMAGE=yubi-core:latest
FROM ${YUBI_CORE_IMAGE}

# ROS distro for subsequent layers (apt installs, sourcing).
ARG ROS_DISTRO=jazzy
ENV ROS_DISTRO=${ROS_DISTRO}

ENV DEBIAN_FRONTEND=noninteractive

SHELL ["/bin/bash", "-c"]

# yubi-specific hardware dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libhidapi-dev && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# yubi-specific ROS packages
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
        python3-hid \
        python3-serial \
        ros-$ROS_DISTRO-rviz2 \
        ros-$ROS_DISTRO-joint-state-publisher \
        ros-$ROS_DISTRO-joint-state-publisher-gui \
        ros-$ROS_DISTRO-robot-state-publisher \
        ros-$ROS_DISTRO-xacro \
        ros-$ROS_DISTRO-usb-cam \
        ros-$ROS_DISTRO-rosbridge-suite && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy yubi packages into existing workspace
COPY ./footpedal_ros /root/ros2_ws/src/footpedal_ros
COPY ./yubi_bringup /root/ros2_ws/src/yubi_bringup
COPY ./yubi_description /root/ros2_ws/src/yubi_description
COPY ./airoa_quest /root/ros2_ws/src/airoa_quest

# Clone RealSense ROS wrapper
WORKDIR /root/ros2_ws/src
RUN git clone https://github.com/IntelRealSense/realsense-ros -b 4.56.4

# Install dependencies and build
WORKDIR /root/ros2_ws
RUN apt-get update -y && \
    rosdep update && \
    rosdep install --from-paths src --ignore-src -r -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*
RUN source /opt/ros/$ROS_DISTRO/setup.bash && \
    source ./install/setup.bash && \
    colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release

# entrypoint
COPY ./docker/ros_entrypoint.sh /root/ros_entrypoint.sh
RUN chmod +x /root/ros_entrypoint.sh
RUN echo "source /root/ros_entrypoint.sh" >> /root/.bashrc

ARG YUBI_GIT_HASH
ARG YUBI_GIT_BRANCH
ENV YUBI_GIT_HASH=$YUBI_GIT_HASH
ENV YUBI_GIT_BRANCH=$YUBI_GIT_BRANCH

WORKDIR /root/ros2_ws
CMD ["/bin/bash"]
