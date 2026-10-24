#!/bin/sh
set -eu
# A fixed dedicated directory prevents accidental deletion of arbitrary mounts.
export PROMETHEUS_MULTIPROC_DIR=/tmp/file-friend-prometheus
test ! -L "$PROMETHEUS_MULTIPROC_DIR"
mkdir -p "$PROMETHEUS_MULTIPROC_DIR"
test -w "$PROMETHEUS_MULTIPROC_DIR"
find "$PROMETHEUS_MULTIPROC_DIR" -mindepth 1 -delete
exec gunicorn -c /app/gunicorn_conf.py 'byteforge_mcp_file_friend_backend:create_app()'
