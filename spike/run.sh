#!/usr/bin/env bash
# Linux x86_64; requires curl, tar, sha256sum, uv and Python 3.13.
set -euo pipefail
spike_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
spike_tmp="$(mktemp -d -t ff-spike.XXXXXXXX)"
trap 'rm -rf -- "$spike_tmp"' EXIT
export UV_CACHE_DIR="$spike_tmp/uv-cache"
curl --fail --silent --show-error --location \
  https://github.com/tus/tusd/releases/download/v2.10.1/tusd_linux_amd64.tar.gz \
  --output "$spike_tmp/tusd_linux_amd64.tar.gz"
(cd "$spike_tmp" && sha256sum --check <<'CHECKSUM'
e8c8d7738275d2d9377db953aecbc242262d3620e77fbd3c1491361d9cea6ac8  tusd_linux_amd64.tar.gz
CHECKSUM
)
tar -xzf "$spike_tmp/tusd_linux_amd64.tar.gz" -C "$spike_tmp"
uv venv --python 3.13 "$spike_tmp/venv"
uv pip install --python "$spike_tmp/venv/bin/python" -r "$spike_dir/requirements.txt"
uv pip install --python "$spike_tmp/venv/bin/python" --require-hashes -r "$spike_dir/../requirements.txt"
uv pip install --python "$spike_tmp/venv/bin/python" --require-hashes -r "$spike_dir/../build-requirements.txt"
uv pip install --python "$spike_tmp/venv/bin/python" --no-deps --no-build-isolation \
  "git+https://github.com/jmazzahacks/byteforge-mcp-file-friend-models.git@1282a681a41cd144b21593ab0820a724b9a485a6" \
  "git+https://github.com/jmazzahacks/byteforge-mcp-file-friend-core.git@25a00d319ae22cd981d77394d4c94eeddc1cb99f"
"$spike_tmp/venv/bin/python" "$spike_dir/validate.py" \
  --tusd "$spike_tmp/tusd_linux_amd64/tusd"

# PostgreSQL lifecycle tests run with the project pytest harness (see README).
