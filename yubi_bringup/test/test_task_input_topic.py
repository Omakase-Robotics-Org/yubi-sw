"""Which task input each box uses, pinned per box/variant.

This used to be a single hardcoded `joy_remap_topic:=…` on the `command:` line
in docker-compose.yml — the same value for every box in the fleet. A stationary
yagura wants the footpedal; yubi1 is stationary but accepts/rejects with the
gripper double-click. Both cannot be right from one global constant, and the
failure is silent: the wrong input just never triggers.

These tests resolve the topic from the real config files in this repo, so they
fail if a variant overlay or the committed yubi1 snapshot ever changes the
input a box gets.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
YUBI_BRINGUP = REPO_ROOT / "yubi_bringup"
CONFIG_ROOT = YUBI_BRINGUP / "config"
YUBI1_SNAPSHOT = REPO_ROOT / "deploy" / "yubi1" / "config-local"

for path in (str(YUBI_BRINGUP),):
    if path not in sys.path:
        sys.path.insert(0, path)

from yubi_bringup.launch_registry import (  # noqa: E402
    AGGREGATED_JOY_TOPIC,
    FOOTPEDAL_JOY_TOPIC,
    resolve_task_input_topic,
)


def layers(variant: str, local: Path | None = None) -> list[Path]:
    """The same common/<variant>/local path list the launch file builds."""
    paths = [
        CONFIG_ROOT / "common" / "yubi_devices.yaml",
        CONFIG_ROOT / variant / "yubi_devices.yaml",
    ]
    paths.append(local if local is not None else CONFIG_ROOT / "local" / "yubi_devices.yaml")
    return paths


def test_portable_box_uses_the_aggregated_stream():
    """A portable box has no pedal — gripper double-click + Quest buttons."""
    assert resolve_task_input_topic(layers("portable")) == AGGREGATED_JOY_TOPIC


def test_plain_stationary_box_uses_the_footpedal():
    """A yagura with a pedal and no per-host override."""
    assert resolve_task_input_topic(layers("stationary")) == FOOTPEDAL_JOY_TOPIC


def test_yubi1_stationary_with_its_local_layer_keeps_the_glove():
    """Regression: yubi1 is stationary but drives the task state machine from
    the gripper double-click. Moving it onto a plain stationary profile must
    not silently hand it the footpedal."""
    local = YUBI1_SNAPSHOT / "yubi_devices.yaml"
    assert local.exists(), f"missing committed yubi1 snapshot: {local}"
    assert resolve_task_input_topic(layers("stationary", local)) == AGGREGATED_JOY_TOPIC


def test_declaring_the_aggregator_is_enough_without_an_explicit_pin(tmp_path):
    """Rule 3: a box that spawns portable_joy_command_node consumes it, even if
    the explicit joy_source_topic pin is lost. Nothing else publishes it."""
    local = tmp_path / "yubi_devices.yaml"
    local.write_text("portable_joy_command_node:\n  ros__parameters: {}\n")
    assert resolve_task_input_topic(layers("stationary", local)) == AGGREGATED_JOY_TOPIC


def test_local_layer_overrides_the_variant(tmp_path):
    """config/local/ beats config/<variant>/ — later layers win."""
    local = tmp_path / "yubi_devices.yaml"
    local.write_text(
        "task_command_dispatch_node:\n"
        "  ros__parameters:\n"
        '    joy_source_topic: "/footpedal_states"\n'
    )
    assert resolve_task_input_topic(layers("portable", local)) == FOOTPEDAL_JOY_TOPIC


def test_launch_argument_overrides_everything(tmp_path):
    assert (
        resolve_task_input_topic(layers("stationary"), override="/some/debug/topic")
        == "/some/debug/topic"
    )


def test_missing_files_are_skipped(tmp_path):
    assert resolve_task_input_topic([tmp_path / "nope.yaml"]) == FOOTPEDAL_JOY_TOPIC


@pytest.mark.parametrize("variant", ["stationary", "portable"])
def test_every_variant_resolves_to_a_published_topic(variant):
    """Whatever a variant resolves to, some node must publish it: the pedal
    topic needs footpedal_node, the aggregated one needs the aggregator."""
    from yubi_bringup.launch_registry import collect_yaml_keys

    paths = layers(variant)
    topic = resolve_task_input_topic(paths)
    keys = collect_yaml_keys(paths)
    publisher = {
        FOOTPEDAL_JOY_TOPIC: "footpedal_node",
        AGGREGATED_JOY_TOPIC: "portable_joy_command_node",
    }[topic]
    assert publisher in keys, (
        f"{variant} resolves to {topic} but does not spawn {publisher}"
    )
