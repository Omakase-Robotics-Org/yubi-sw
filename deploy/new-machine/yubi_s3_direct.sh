#!/usr/bin/env bash
# Reference wrapper for the laptop-direct S3 uploader. NO SECRETS — only cert
# PATHS and non-secret identifiers. `make s3-creds` REGENERATES this file at
# $HOME/yubi_s3_direct.sh with this machine's unique IOT_THING; prefer that over
# editing this copy. If you run it by hand, set IOT_THING to THIS device's name
# (never leave it as another box's name).
set -euo pipefail
exec 9>/tmp/yubi_s3_direct.lock; flock -n 9 || exit 0

export IOT_CERT="$HOME/iot/device.cert.pem"
export IOT_KEY="$HOME/iot/device.private.key"
export IOT_ROOT_CA="$HOME/iot/AmazonRootCA1.pem"

# Non-secret identifiers (safe to commit). Override per machine as needed.
export IOT_ENDPOINT="${IOT_ENDPOINT:-c7365kceqmnid.credentials.iot.ap-northeast-1.amazonaws.com}"
export IOT_ROLE_ALIAS="${IOT_ROLE_ALIAS:-yubi-uploader-alias}"
export IOT_THING="${IOT_THING:-$(hostname)}"   # MUST be unique per device
export S3_BUCKET="${S3_BUCKET:-omakase-robotics-data}"

python3 "$HOME/yubi_s3_direct.py" "$@"
