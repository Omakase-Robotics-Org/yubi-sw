#!/usr/bin/env python3
"""Prepare a local yubi-sw checkout for first use on one machine."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCAL_CONFIG_DIR = ROOT / "yubi_bringup" / "config" / "local"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

ROBOT_CONFIG_TEMPLATE = """# Per-host overrides for this machine.
/**:
  ros__parameters:
    api_key: "WRITE_HERE"
    base_url: "http://WRITE_HERE:8000/api"
"""

YUBI_DEVICES_TEMPLATE = """# Per-host overrides for this machine.
quest_bridge_node:
  ros__parameters:
    quest_ip: "WRITE_HERE"
"""


def detect_variant(default: str = "stationary") -> str:
    if not ENV_FILE.exists():
        return default
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "ROBOT_VARIANT":
            detected = value.strip()
            if detected in {"stationary", "portable"}:
                return detected
    return default


def ensure_file(path: Path, content: str) -> str:
    if path.exists():
        return "exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="ascii")
    return "created"


def command_status(argv: list[str]) -> str:
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "blocked"

    return "ok" if completed.returncode == 0 else "error"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--variant",
        choices=("stationary", "portable"),
        default=detect_variant(),
        help="Runtime config variant to generate.",
    )
    args = parser.parse_args(argv)

    summary: list[str] = []

    if not ENV_FILE.exists():
        if not ENV_EXAMPLE.exists():
            print(f"missing template: {ENV_EXAMPLE}", file=sys.stderr)
            return 1
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        summary.append("created .env from .env.example")
    else:
        summary.append("kept existing .env")

    summary.append(
        f"{ensure_file(LOCAL_CONFIG_DIR / 'robot_config.yaml', ROBOT_CONFIG_TEMPLATE)} "
        "yubi_bringup/config/local/robot_config.yaml"
    )
    summary.append(
        f"{ensure_file(LOCAL_CONFIG_DIR / 'yubi_devices.yaml', YUBI_DEVICES_TEMPLATE)} "
        "yubi_bringup/config/local/yubi_devices.yaml"
    )

    build_cmd = [
        sys.executable,
        str(ROOT / "yubi_bringup" / "tools" / "build_runtime_configs.py"),
        "--variant",
        args.variant,
        "--with-local",
    ]
    build = subprocess.run(build_cmd, check=False, text=True, capture_output=True)
    if build.stdout:
        print(build.stdout, end="")
    if build.returncode != 0:
        if build.stderr:
            print(build.stderr, file=sys.stderr, end="")
        return build.returncode
    summary.append(f"generated runtime config for variant={args.variant}")

    print("")
    print("Bootstrap summary:")
    for item in summary:
        print(f"- {item}")

    print("")
    print("Prerequisite check:")
    for label, cmd in (
        ("docker", ["docker", "--version"]),
        ("docker compose", ["docker", "compose", "version"]),
        ("make", ["make", "--version"]),
    ):
        print(f"- {label}: {command_status(cmd)}")

    print("")
    print("Next steps:")
    print("- Edit .env if this host needs non-default paths or portable mode.")
    print("- Replace WRITE_HERE in yubi_bringup/config/local/*.yaml.")
    print("- Run the host setup scripts in tools/ before starting containers.")
    print("- Start the stack with `docker compose up -d` after Docker is available.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
