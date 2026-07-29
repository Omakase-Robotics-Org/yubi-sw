#!/usr/bin/env python3
"""Data collection launch — bringup + data-collection-specific nodes.

Layers on top of `yubi_bringup.launch.py`:
- Includes the bringup file (USB cameras, RealSense, Quest bridge, encoder)
- Spawns DATA_COLLECTION_NODE_REGISTRY entries that are present in the merged
  yubi_devices.yaml (footpedal / task_command_dispatch)
- Hosts the rosbridge_websocket (always on, fixed-port service)
"""

from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    LogInfo,
    OpaqueFunction,
    SetLaunchConfiguration,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import (
    EnvironmentVariable,
    LaunchConfiguration,
)
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from pathlib import Path

from yubi_bringup.launch_registry import (
    DATA_COLLECTION_NODE_REGISTRY,
    collect_yaml_keys,
    resolve_task_input_topic,
    select_nodes,
)


def _spawn_data_collection_nodes(context, *_args, **_kwargs):
    # Re-resolve paths from this launch context so the variant arg is honored.
    share = Path(FindPackageShare("yubi_bringup").find("yubi_bringup"))
    variant = context.perform_substitution(LaunchConfiguration("robot_variant"))
    paths = [
        share / "config" / "common" / "yubi_devices.yaml",
        share / "config" / variant / "yubi_devices.yaml",
        share / "config" / "local" / "yubi_devices.yaml",
    ]
    keys = collect_yaml_keys(paths)
    params = [str(p) for p in paths if p.exists()]

    # The task input is a per-box setting resolved from the same
    # common/<variant>/local layering as everything else. An explicit
    # joy_remap_topic:= argument still wins, for one-off debugging.
    override = context.perform_substitution(LaunchConfiguration("joy_remap_topic"))
    topic = resolve_task_input_topic(paths, override=override)

    # task_command_dispatch_node's remap reads LaunchConfiguration at execution
    # time, so setting it here — before the nodes in the returned list run —
    # is what makes the resolved value take effect.
    return [
        LogInfo(msg=f"[yubi] task input -> {topic} (variant={variant})"),
        SetLaunchConfiguration("joy_remap_topic", topic),
        *select_nodes(DATA_COLLECTION_NODE_REGISTRY, keys, params),
    ]


def generate_launch_description():
    pkg_share = Path(FindPackageShare("yubi_bringup").find("yubi_bringup"))

    bringup_include = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(
            str(pkg_share / "launch" / "yubi_bringup.launch.py")
        ),
        launch_arguments={
            "robot_variant": LaunchConfiguration("robot_variant"),
        }.items(),
    )

    rosbridge = Node(
        package="rosbridge_server",
        executable="rosbridge_websocket",
        name="rosbridge_websocket",
        output="screen",
        respawn=True,
        respawn_delay=2.0,
        parameters=[{"port": 9090}],
    )

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "robot_variant",
                default_value=EnvironmentVariable(
                    "ROBOT_VARIANT", default_value="stationary"
                ),
                description="Robot variant: stationary | portable (selects config/<variant>/...)",
            ),
            DeclareLaunchArgument(
                "joy_remap_topic",
                default_value="",
                description=(
                    "Override the topic remapped to /joy for "
                    "task_command_dispatch_node. Leave empty (the default) to "
                    "resolve it from config/{common,<variant>,local}/"
                    "yubi_devices.yaml — see resolve_task_input_topic(). Set it "
                    "only for one-off debugging; a box's normal input belongs in "
                    "its config layer, not on the command line."
                ),
            ),
            bringup_include,
            rosbridge,
            OpaqueFunction(function=_spawn_data_collection_nodes),
        ]
    )
