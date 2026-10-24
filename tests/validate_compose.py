"""Explicit local Compose smoke test; requires FF_SERVER_URL and FF_TOKEN. Creates/deletes one 100 MiB upload."""

import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from byteforge_mcp_file_friend_api_python import Client

origin = os.environ["FF_SERVER_URL"]
token = os.environ["FF_TOKEN"]


def check(path, expected, headers=None, method="GET"):
    try:
        response = urllib.request.urlopen(
            urllib.request.Request(origin + path, headers=headers or {}, method=method)
        )
    except urllib.error.HTTPError as response:
        assert response.code == expected, (path, response.code)
        return
    with response:
        assert response.status == expected, (path, response.status)


with tempfile.TemporaryDirectory(prefix="ff-compose-check-") as directory:
    root = Path(directory)
    source = root / "fixture.bin"
    with source.open("wb") as f:
        for _ in range(100):
            f.write(bytes(range(256)) * 4096)
    client = Client(origin, token=token, cache_dir=root / "cache", timeout=120)
    start = time.monotonic()
    ref = client.upload(source)
    client.download(ref, root / "copy.bin")
    assert source.read_bytes() == (root / "copy.bin").read_bytes()
    check("/files/", 401, method="POST")
    check("/files/", 405)
    check("/internal/hooks", 404)
    check("/files/", 400, {"X-HTTP-Method-Override": "DELETE"})
    check(urllib.parse.urlsplit(ref.url).path, 206, {"Range": "bytes=0-9"})
    check("/ready", 200)
    print(f"100 MiB Docker upload/download and proxy checks passed ({time.monotonic() - start:.1f}s).")
    client.json_request("DELETE", f"{origin}/api/files/{ref.id}", True)
    check(urllib.parse.urlsplit(ref.url).path, 404)
