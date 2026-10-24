#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
publish=false
options=()
for arg in "$@"; do
    case "$arg" in
        --no-cache) options+=(--no-cache) ;;
        --publish) publish=true ;;
        *) echo "Usage: bash build-publish.sh [--no-cache] [--publish]" >&2; exit 2 ;;
    esac
done
if "$publish" && [ ! -f LICENSE ]; then
    echo "Choose and add the project license before publishing." >&2
    exit 1
fi
registry="${FF_IMAGE:-ghcr.io/jmazzahacks/byteforge-mcp-file-friend}"
current=0
if [ -f VERSION ]; then current=$(cat VERSION); fi
[[ "$current" =~ ^[0-9]+$ ]] || { echo "Invalid VERSION" >&2; exit 1; }
version=$((current + 1))
docker build "${options[@]}" --platform linux/amd64 -t "$registry:$version" .
if "$publish"; then
    docker tag "$registry:$version" "$registry:latest"
    docker push "$registry:$version"
    docker push "$registry:latest"
    echo "$version" > VERSION
fi
