"""Optional real-tusd integration: set TUSD_BIN to the pinned v2.10.1 binary."""

import base64
import hashlib
import os
import socket
import subprocess
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from byteforge_mcp_file_friend_core.tusd import Tusd
from byteforge_mcp_file_friend_models import FriendConfig
from werkzeug.serving import WSGIRequestHandler, make_server

from byteforge_mcp_file_friend_backend import create_app


class QuietHandler(WSGIRequestHandler):
    def log(self, type: str, message: str, *args: object) -> None:
        pass


@pytest.mark.skipif(not os.environ.get("TUSD_BIN"), reason="Set TUSD_BIN for real tusd integration")
def test_actual_hooks_worker_and_delete(tmp_path: Path) -> None:
    binary = os.environ["TUSD_BIN"]
    version = subprocess.check_output([binary, "-version"], text=True)
    assert "Version: v2.10.1\n" in version
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    tusd_url = f"http://127.0.0.1:{port}/files/"
    app = create_app(
        FriendConfig(
            tmp_path,
            "https://ff.example",
            tusd_url=tusd_url,
            hook_token="test-hook",
            dev_admin_token="test-admin",
            min_free=0,
        )
    )
    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    command = [
        binary,
        "-host",
        "127.0.0.1",
        "-port",
        str(port),
        "-upload-dir",
        str(tmp_path / "uploads"),
        "-disable-download",
        "-disable-concatenation",
        "-max-size",
        "1048576",
        "-hooks-http",
        f"http://tusd:test-hook@127.0.0.1:{server.server_port}/internal/hooks",
        "-hooks-enabled-events",
        "pre-create,post-create,post-finish,pre-terminate,post-terminate",
        "-hooks-http-timeout",
        "2s",
        "-hooks-http-retry",
        "0",
        "-shutdown-timeout",
        "1s",
    ]

    def send(
        method: str, url: str = "/files/", data: bytes | None = None, headers: dict | None = None
    ) -> tuple:
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request(method, urlsplit(url).path, data, {"Tus-Resumable": "1.0.0", **(headers or {})})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    process = None
    try:
        with (tmp_path / "tusd.log").open("w") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            for _ in range(100):
                try:
                    if send("OPTIONS")[0] == 200:
                        break
                except OSError:
                    time.sleep(0.03)
            else:
                pytest.fail("tusd did not start")
            store = app.extensions["ff_store"]
            client = app.test_client()
            adapter = Tusd(tusd_url)
            for body, valid in ((b"whole-file", True), (b"wrong-hash", False), (b"", True)):
                digest = hashlib.sha256(body if valid else b"expected").hexdigest()
                metadata = {"filename": "fixture.bin", "sha256": digest}
                encoded = ",".join(
                    f"{key} {base64.b64encode(value.encode()).decode()}" for key, value in metadata.items()
                )
                status, headers, _ = send(
                    "POST", headers={"Upload-Length": str(len(body)), "Upload-Metadata": encoded}
                )
                assert status == 201
                url = headers["Location"]
                file_id = url.rsplit("/", 1)[1]
                if body:
                    assert (
                        send(
                            "PATCH",
                            url,
                            body,
                            {"Upload-Offset": "0", "Content-Type": "application/offset+octet-stream"},
                        )[0]
                        == 204
                    )
                store.reconcile(adapter.terminate)
                status = client.get(f"/api/files/{file_id}")
                assert status.json["state"] == ("ready" if valid else "failed")
                if valid:
                    assert store.published(store.read(file_id)).read_bytes() == body
                    response = client.delete(
                        f"/api/files/{file_id}", headers={"Authorization": "Bearer test-admin"}
                    )
                    assert response.status_code == 202
                    store.reconcile(adapter.terminate)
                    assert client.get(f"/api/files/{file_id}").status_code == 410
                assert send("HEAD", url)[0] == 404
                assert not store.published(store.read(file_id)).exists()
            # A direct tus DELETE also invokes pre-terminate and unpublishes.
            status, headers, _ = send(
                "POST",
                headers={
                    "Upload-Length": "0",
                    "Upload-Metadata": encoded.replace(
                        base64.b64encode(digest.encode()).decode(),
                        base64.b64encode(hashlib.sha256(b"").hexdigest().encode()).decode(),
                    ),
                },
            )
            assert status == 201
            url = headers["Location"]
            file_id = url.rsplit("/", 1)[1]
            store.reconcile(adapter.terminate)
            assert store.published(store.read(file_id)).exists()
            assert send("DELETE", url)[0] == 204
            assert not store.published(store.read(file_id)).exists()
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        server.shutdown()
        server.server_close()
        thread.join(2)
