#!/usr/bin/env bash
exec 9>/tmp/yubi_s3_direct.lock; flock -n 9 || exit 0
export IOT_CERT=$HOME/iot/device.cert.pem
export IOT_KEY=$HOME/iot/device.private.key
export IOT_ROOT_CA=$HOME/iot/AmazonRootCA1.pem
export S3_BUCKET=omakase-robotics-data
# GC local MinIO copies 14 days after their upload is HEAD-verified in AWS S3
export YUBI_GC_DAYS=${YUBI_GC_DAYS:-14}
python3 $HOME/yubi_s3_direct.py "$@"
