#!/usr/bin/env bash
exec 9>/tmp/yubi_s3_direct.lock; flock -n 9 || exit 0
export IOT_CERT=$HOME/iot/device.cert.pem
export IOT_KEY=$HOME/iot/device.private.key
export IOT_ROOT_CA=$HOME/iot/AmazonRootCA1.pem
export S3_BUCKET=omakase-robotics-data
python3 $HOME/yubi_s3_direct.py "$@"
