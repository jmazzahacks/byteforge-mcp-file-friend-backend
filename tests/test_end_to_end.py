import hashlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import replace
from http.client import HTTPConnection
from pathlib import Path
from urllib.error import HTTPError

import pytest
from byteforge_mcp_file_friend_core.tusd import Tusd
from byteforge_mcp_file_friend_models import FriendConfig, validate_id
from flask import Response, abort, request, send_file
from test_live_tusd import QuietHandler
from werkzeug.serving import make_server

from byteforge_mcp_file_friend_backend import create_app

client_package = pytest.importorskip("byteforge_mcp_file_friend_api_python", reason="Independent client extraction is pending")
Client = client_package.Client
open_reference = client_package.open_reference

@pytest.fixture
def stack(tmp_path: Path):
    binary = os.environ.get("TUSD_BIN")
    if not binary:
        pytest.skip("Set TUSD_BIN for end-to-end transfers")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        tusd_port = sock.getsockname()[1]
    settings = FriendConfig(
        tmp_path / "data",
        "http://127.0.0.1",
        hook_token="hook",
        dev_admin_token="admin",
        tusd_url=f"http://127.0.0.1:{tusd_port}/files/",
        min_free=0,
    )
    app = create_app(settings)
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
    state = {"drop": False, "posts": 0, "bad_location": False}

    @app.route("/files/", methods=["POST", "OPTIONS"])
    @app.route("/files/<file_id>", methods=["HEAD", "PATCH", "DELETE"])
    def proxy(file_id: str | None = None):
        if request.headers.get("Authorization") != "Bearer admin":
            abort(401)
        if file_id:
            validate_id(file_id)
        if request.method == "POST":
            state["posts"] += 1
        connection = HTTPConnection("127.0.0.1", tusd_port, timeout=5)
        try:
            headers = {
                key: value for key, value in request.headers if key.lower() not in ("host", "connection")
            }
            connection.request(request.method, request.path, request.get_data(), headers)
            upstream = connection.getresponse()
            body = upstream.read()
            outgoing = {
                key: value
                for key, value in upstream.getheaders()
                if key.lower() not in ("connection", "transfer-encoding", "content-length")
            }
            if "Location" in outgoing:
                outgoing["Location"] = base + "/files/" + outgoing["Location"].rsplit("/", 1)[1]
                if state["bad_location"]:
                    outgoing["Location"] = "https://evil.example/files/" + "f" * 32
            if request.method == "PATCH" and state["drop"]:
                state["drop"] = False
                return Response("lost final acknowledgement", status=503)
            return Response(body, status=upstream.status, headers=outgoing)
        finally:
            connection.close()

    @app.get("/f/<file_id>/<filename>")
    def download(file_id: str, filename: str):
        validate_id(file_id)
        return send_file(settings.root / "done" / file_id / filename, conditional=True, as_attachment=True)

    server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
    base = f"http://127.0.0.1:{server.server_port}"
    settings = replace(settings, public_url=base)
    app.extensions["ff_config"] = settings
    store = app.extensions["ff_store"]
    store.config = settings
    store.public_url = base
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log = (tmp_path / "services.log").open("w")
    tusd = subprocess.Popen(
        [
            binary,
            "-host",
            "127.0.0.1",
            "-port",
            str(tusd_port),
            "-upload-dir",
            str(settings.root / "uploads"),
            "-disable-download",
            "-disable-concatenation",
            "-max-size",
            "1048576",
            "-hooks-http",
            f"http://tusd:hook@127.0.0.1:{server.server_port}/internal/hooks",
            "-hooks-enabled-events",
            "pre-create,post-create,post-finish,pre-terminate,post-terminate",
            "-hooks-http-timeout",
            "2s",
            "-hooks-http-retry",
            "0",
            "-shutdown-timeout",
            "1s",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    env = {
        **os.environ,
        "FF_DATA_DIR": str(settings.root),
        "FF_PUBLIC_URL": base,
        "FF_TUSD_URL": settings.tusd_url,
        "FF_HOOK_TOKEN": "hook",
        "FF_DEV_ADMIN_TOKEN": "admin",
        "FF_SERVER_URL": base,
        "FF_TOKEN": "admin",
        "FF_CACHE_DIR": str(tmp_path / "cache"),
    }
    worker = None
    try:
        for _ in range(100):
            try:
                connection = HTTPConnection("127.0.0.1", tusd_port, timeout=1)
                connection.request("OPTIONS", "/files/")
                response = connection.getresponse()
                response.read()
                connection.close()
                if response.status == 200:
                    break
            except OSError:
                time.sleep(0.03)
        worker = subprocess.Popen(
            [sys.executable, "-m", "byteforge_mcp_file_friend_core.worker", "--interval", "0.1"],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        yield base, store, state, env, worker
    finally:
        for process in (worker, tusd):
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
        log.close()


def test_real_cli_upload_download_and_reference(stack, tmp_path: Path) -> None:
    base, store, state, env, worker = stack
    source = tmp_path / "sample.bin"
    source.write_bytes(bytes(range(256)) * 1024)
    state["drop"] = True
    upload = subprocess.run(
        [sys.executable, "-m", "byteforge_mcp_file_friend_api_python.cli", "upload", str(source), "--chunk-size", "32K"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    ref = json.loads(upload.stdout)
    assert ref["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "bytes" in upload.stderr
    target = tmp_path / "received.bin"
    subprocess.run(
        [sys.executable, "-m", "byteforge_mcp_file_friend_api_python.cli", "download", ref["id"], "-o", str(target)],
        env=env,
        capture_output=True,
        timeout=15,
        check=True,
    )
    assert target.read_bytes() == source.read_bytes()
    with open_reference(ref, server_url=base, local_root=store.root) as local:
        assert local.read_bytes() == source.read_bytes()
    with open_reference(ref, server_url=base) as downloaded:
        assert downloaded.read_bytes() == source.read_bytes()
    assert not downloaded.exists()
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    assert Client(base, "admin", cache_dir=tmp_path / "cache2").upload(empty).size == 0
    assert worker.poll() is None


def test_resume_after_client_process_death_and_reject_mutation(stack, tmp_path: Path) -> None:
    base, store, state, env, _worker = stack
    source = tmp_path / "source.bin"
    source.write_bytes(b"a" * 100000)
    child = """
import os, sys
from pathlib import Path
from byteforge_mcp_file_friend_api_python import Client
def crash(done, total):
    os._exit(23)
Client(sys.argv[1], 'admin', cache_dir=Path(sys.argv[3]), progress=crash).upload(Path(sys.argv[2]), chunk_size=8192)
"""
    cache = tmp_path / "cache"
    result = subprocess.run(
        [sys.executable, "-c", child, base, str(source), str(cache)], env=env, timeout=10, check=False
    )
    assert result.returncode == 23
    posts = state["posts"]
    reference = Client(base, "admin", cache_dir=cache).upload(source, chunk_size=8192)
    assert state["posts"] == posts
    assert store.published(store.read(reference.id)).read_bytes() == source.read_bytes()
    subprocess.run(
        [sys.executable, "-c", child, base, str(source), str(cache)], env=env, timeout=10, check=False
    )
    source.write_bytes(b"a" * 99999 + b"b")
    with pytest.raises(ValueError, match="changed"):
        Client(base, "admin", cache_dir=cache).upload(source)
    assert (
        Client(base, "admin", cache_dir=cache).upload(source, restart=True).sha256
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )


def test_expiry_hook_rechecks_activity_and_orphan_recovery(stack, tmp_path: Path) -> None:
    base, store, _state, _env, worker = stack
    worker.terminate()
    worker.wait(timeout=5)
    store.config = replace(store.config, incomplete_ttl=1, reservation_ttl=1)
    client = Client(base, "admin", cache_dir=tmp_path / "cache")
    record = client.create_upload(
        {"filename": "partial", "sha256": "a" * 64, "ttl": 86400, "size": 100, "server": base}
    )
    file_id = record["url"].rsplit("/", 1)[1]
    with store.lock(file_id):
        metadata = store.read(file_id)
        metadata["created_at"] -= 10
        store.write(metadata)
    store.source(file_id).write_bytes(b"a")
    os.utime(store.source(file_id), (0, 0))
    assert store.expiry_due(store.read(file_id))
    store.source(file_id).write_bytes(b"aa")
    adapter = Tusd(store.config.tusd_url)
    assert adapter.expire(file_id) is False
    assert store.source(file_id).exists()
    os.utime(store.source(file_id), (0, 0))
    assert store.reconcile(adapter.terminate, adapter.expire) == []
    assert store.read(file_id)["state"] == "expired"
    orphan = store.reserve({"filename": "orphan", "sha256": "b" * 64}, 123, 1000, 0, 16)
    with store.lock(orphan["id"]):
        orphan["created_at"] -= 10
        store.write(orphan)
    assert store.reconcile(adapter.terminate, adapter.expire) == []
    assert store.read(orphan["id"])["state"] == "expired"


def test_upload_location_cannot_leak_token(stack, tmp_path: Path) -> None:
    base, _store, state, _env, _worker = stack
    state["bad_location"] = True
    source = tmp_path / "file"
    source.write_bytes(b"data")
    with pytest.raises(ValueError, match="different"):
        Client(base, "admin", cache_dir=tmp_path / "cache").upload(source)


def test_upload_recovers_across_actual_postgres_restart(stack, tmp_path):
    """Only enabled by the disposable-cluster runner; never targets Athena."""
    control = os.environ.get("FF_TEST_PG_CTL")
    data = os.environ.get("FF_TEST_PG_DATA")
    if not control or not data:
        pytest.skip("Disposable cluster control required for actual outage test")
    assert Path(data).resolve().is_relative_to(Path("/tmp"))
    assert os.environ["FF_DB_HOST"] == "127.0.0.1"
    assert os.environ["FF_DB_NAME"].endswith("_test")
    base, store, _state, _env, _worker = stack
    client = Client(base, token="admin", cache_dir=tmp_path / "cache", timeout=30)
    body = b"outage-fixture"
    identity = {
        "filename": "outage.bin",
        "sha256": hashlib.sha256(body).hexdigest(),
        "size": len(body),
        "ttl": 60,
    }
    created = client.create_upload(identity)
    file_id = created["url"].rsplit("/", 1)[1]
    headers = {
        "Tus-Resumable": "1.0.0",
        "Upload-Offset": "0",
        "Content-Type": "application/offset+octet-stream",
    }
    with client.request("PATCH", created["url"], body=body[:3], headers=headers, authenticated=True):
        pass
    subprocess.run([control, "-D", data, "-m", "fast", "stop"], check=True, capture_output=True)
    try:
        for path in ("/ready", f"/api/files/{file_id}"):
            with pytest.raises(HTTPError) as caught:
                client.json_request("GET", base + path)
            assert caught.value.code == 503
        with pytest.raises(HTTPError) as caught:
            client.create_upload(identity)
        assert caught.value.code >= 500
        headers["Upload-Offset"] = "3"
        # post-finish notification can fail while bytes still commit in tusd.
        try:
            with client.request("PATCH", created["url"], body=body[3:], headers=headers, authenticated=True):
                pass
        except HTTPError as error:
            assert error.code >= 500
        assert store.source(file_id).read_bytes() == body
        assert not (store.root / "done" / file_id).exists()
    finally:
        subprocess.run(
            [
                control,
                "-D",
                data,
                "-l",
                str(Path(data).parent / "server.log"),
                "-o",
                f"-h 127.0.0.1 -p {int(os.environ['FF_DB_PORT'])} -k {Path(data).parent}",
                "start",
            ],
            check=True,
            capture_output=True,
        )
    ref = client.reference(file_id)
    assert client.download(ref, tmp_path / "recovered.bin").read_bytes() == body
    assert not (store.root / "meta").exists()
