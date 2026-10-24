"""Isolated protocol probes, NOT a File Friend server implementation.

Run with Python containing tuspy==1.1.0 and --tusd pointing to tusd v2.10.1.
All listeners bind to loopback; files live in a TemporaryDirectory.
"""

import argparse
import base64
import hashlib
import http.client
import importlib.metadata
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


class Hooks(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        event = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        upload = event["Event"]["Upload"]
        kind = event["Type"]
        mode = upload["MetaData"].get("mode", "")
        with self.server.condition:
            self.server.events.append(event)
            self.server.condition.notify_all()
        response = {}
        status = 200
        if kind == "pre-create":
            if mode == "reject-create":
                response = {"RejectUpload": True, "HTTPResponse": {"StatusCode": 403}}
            elif mode == "custom-id":
                response = {"ChangeFileInfo": {"ID": "a" * 32}}
        if kind == "pre-finish":
            if mode == "reject-finish":
                response = {"RejectUpload": True}
            elif mode == "error-response":
                response = {"HTTPResponse": {"StatusCode": 422, "Body": "hash mismatch"}}
            elif mode == "body-204":
                response = {"HTTPResponse": {"Body": '{"ready":true}'}}
            elif mode == "hook-error":
                status = 500
            elif mode == "hook-timeout":
                time.sleep(0.6)
            elif mode == "drop-response":
                self.server.finish_gate.wait(timeout=2)
        body = json.dumps(response).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected when testing hook timeout.


class Spike(unittest.TestCase):
    tusd = None

    @classmethod
    def setUpClass(cls):
        version = subprocess.check_output([cls.tusd, "-version"], text=True)
        if "Version: v2.10.1\n" not in version:
            raise RuntimeError(f"Expected tusd v2.10.1; got {version}")
        if importlib.metadata.version("tuspy") != "1.1.0":
            raise RuntimeError("Expected tuspy==1.1.0")
        cls.temp = tempfile.TemporaryDirectory(prefix="ff-validation-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.uploads = cls.root / "uploads"
        cls.uploads.mkdir()
        cls.hooks = ThreadingHTTPServer(("127.0.0.1", 0), Hooks)
        cls.hooks.daemon_threads = True
        cls.hooks.events = []
        cls.hooks.condition = threading.Condition()
        cls.hooks.finish_gate = threading.Event()
        threading.Thread(target=cls.hooks.serve_forever, daemon=True).start()
        cls.addClassCleanup(cls.hooks.server_close)
        cls.addClassCleanup(cls.hooks.shutdown)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.base = f"http://127.0.0.1:{cls.port}/files/"
        cls.log = (cls.root / "tusd.log").open("w+")
        cls.addClassCleanup(cls.log.close)
        cls.command = [
            cls.tusd,
            "-host",
            "127.0.0.1",
            "-port",
            str(cls.port),
            "-upload-dir",
            str(cls.uploads),
            "-max-size",
            "10485760",
            "-disable-download",
            "-disable-concatenation",
            "-hooks-http",
            f"http://127.0.0.1:{cls.hooks.server_port}/",
            "-hooks-enabled-events",
            "pre-create,post-create,pre-finish,post-finish,post-terminate",
            "-hooks-http-timeout",
            "300ms",
            "-hooks-http-retry",
            "0",
            "-shutdown-timeout",
            "1s",
            "-filelock-holder-poll-interval",
            "50ms",
            "-filelock-acquirer-poll-interval",
            "50ms",
        ]
        cls.addClassCleanup(cls.stop_tusd)
        cls.start_tusd()

    @classmethod
    def start_tusd(cls):
        cls.proc = subprocess.Popen(cls.command, stdout=cls.log, stderr=subprocess.STDOUT)
        for _ in range(100):
            if cls.proc.poll() is not None:
                cls.log.seek(0)
                raise RuntimeError(cls.log.read())
            try:
                conn = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=1)
                conn.request("OPTIONS", "/files/")
                resp = conn.getresponse()
                resp.read()
                conn.close()
                if resp.status == 200:
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("tusd did not start")

    @classmethod
    def stop_tusd(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait()

    def request(self, method, url="/files/", body=None, headers=None):
        path = urlsplit(url).path
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body, {"Tus-Resumable": "1.0.0", **(headers or {})})
            resp = conn.getresponse()
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        finally:
            conn.close()

    def create(self, size, mode="", body=None, metadata=None):
        metadata = {"filename": "fixture.bin", "mode": mode, **(metadata or {})}
        headers = {
            "Upload-Length": str(size),
            "Upload-Metadata": ",".join(
                f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in metadata.items()
            ),
        }
        if body is not None:
            headers["Content-Type"] = "application/offset+octet-stream"
        return self.request("POST", body=body, headers=headers)

    def patch(self, url, data, offset=0):
        return self.request(
            "PATCH",
            url,
            data,
            {"Upload-Offset": str(offset), "Content-Type": "application/offset+octet-stream"},
        )

    def wait_hook(self, kind, file_id):
        deadline = time.monotonic() + 3
        with self.hooks.condition:
            while True:
                matches = [
                    e
                    for e in self.hooks.events
                    if e["Type"] == kind and e["Event"]["Upload"]["ID"] == file_id
                ]
                if matches:
                    return matches[-1]
                remaining = deadline - time.monotonic()
                self.assertGreater(remaining, 0, f"Missing {kind} for {file_id}")
                self.hooks.condition.wait(remaining)

    def file_id(self, url):
        return url.rstrip("/").rsplit("/", 1)[1]

    def test_01_extensions_and_download_disabled(self):
        status, headers, _ = self.request("OPTIONS")
        self.assertEqual(status, 200)
        extensions = set(headers["tus-extension"].split(","))
        print(f"\nAdvertised extensions: {sorted(extensions)}", flush=True)
        self.assertIn("creation-with-upload", extensions)
        self.assertNotIn("checksum", extensions)
        self.assertNotIn("concatenation", extensions)
        _, headers, _ = self.create(1)
        url = headers["location"]
        self.assertEqual(self.patch(url, b"x")[0], 204)
        self.assertEqual(self.request("GET", url)[0], 405)

    def test_02_id_format_and_custom_id(self):
        _, headers, _ = self.create(1)
        self.assertRegex(self.file_id(headers["location"]), r"^[0-9a-f]{32}$")
        _, headers, _ = self.create(1, "custom-id")
        self.assertEqual(self.file_id(headers["location"]), "a" * 32)

    def test_03_precreate_rejects(self):
        self.assertEqual(self.create(1, "reject-create")[0], 403)

    def test_04_prefinish_reject_flag_does_not_reject(self):
        _, headers, _ = self.create(3, "reject-finish")
        url = headers["location"]
        self.assertEqual(self.patch(url, b"abc")[0], 204)
        self.assertEqual(self.request("HEAD", url)[1]["upload-offset"], "3")
        self.assertEqual((self.uploads / self.file_id(url)).read_bytes(), b"abc")

    def test_05_error_response_does_not_rollback(self):
        _, headers, _ = self.create(3, "error-response")
        url = headers["location"]
        self.assertEqual(self.patch(url, b"abc")[0], 422)
        self.assertEqual(self.request("HEAD", url)[1]["upload-offset"], "3")
        self.assertEqual((self.uploads / self.file_id(url)).read_bytes(), b"abc")

    def test_06_204_cannot_deliver_json_body(self):
        _, headers, _ = self.create(1, "body-204")
        status, _, body = self.patch(headers["location"], b"x")
        self.assertEqual((status, body), (204, b""))

    def test_07_hook_failure_and_timeout_leave_complete_bytes(self):
        for mode in ("hook-error", "hook-timeout"):
            with self.subTest(mode=mode):
                _, headers, _ = self.create(3, mode)
                url = headers["location"]
                self.assertEqual(self.patch(url, b"abc")[0], 500)
                self.assertEqual(self.request("HEAD", url)[1]["upload-offset"], "3")
                info = json.loads((self.uploads / (self.file_id(url) + ".info")).read_text())
                self.assertEqual(info["Size"], (self.uploads / self.file_id(url)).stat().st_size)

    def test_08_zero_byte_and_creation_with_upload(self):
        for data in (b"", b"abc"):
            with self.subTest(size=len(data)):
                status, headers, _ = self.create(len(data), body=data)
                self.assertEqual(status, 201)
                file_id = self.file_id(headers["location"])
                self.wait_hook("post-finish", file_id)
                self.assertEqual((self.uploads / file_id).read_bytes(), data)

    def test_09_recover_offset_after_lost_final_response(self):
        _, headers, _ = self.create(3, "drop-response")
        url = headers["location"]
        file_id = self.file_id(url)
        # Hold the hook so the final response cannot arrive before disconnecting.
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            sock.sendall(
                (
                    f"PATCH {urlsplit(url).path} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.port}\r\n"
                    "Tus-Resumable: 1.0.0\r\nUpload-Offset: 0\r\n"
                    "Content-Type: application/offset+octet-stream\r\n"
                    "Content-Length: 3\r\n\r\nabc"
                ).encode()
            )
            self.wait_hook("pre-finish", file_id)
            sock.shutdown(socket.SHUT_RDWR)
        finally:
            sock.close()
            self.hooks.finish_gate.set()
        status, recovered, _ = self.request("HEAD", url)
        self.assertEqual((status, recovered["upload-offset"]), (200, "3"))
        self.assertEqual((self.uploads / file_id).read_bytes(), b"abc")
        # This establishes transport completion only, not application verification.

    def test_10_termination_does_not_remove_published_hardlink(self):
        _, headers, _ = self.create(3)
        url = headers["location"]
        self.assertEqual(self.patch(url, b"abc")[0], 204)
        file_id = self.file_id(url)
        src = self.uploads / file_id
        published = self.root / f"published-{file_id}"
        os.link(src, published)
        self.assertEqual(src.stat().st_ino, published.stat().st_ino)
        self.assertEqual(self.request("DELETE", url)[0], 204)
        self.wait_hook("post-terminate", file_id)
        self.assertFalse(src.exists())
        self.assertFalse(src.with_suffix(".info").exists())
        self.assertEqual(published.read_bytes(), b"abc")
        self.assertEqual(self.request("HEAD", url)[0], 404)
        published.unlink()

    def test_11_tuspy_cross_process_resume(self):
        fixture = self.root / "resume.bin"
        fixture.write_bytes(bytes(range(256)) * 1024)
        storage = self.root / "resume.json"
        child = """
import json, sys
from tusclient.client import TusClient
from tusclient.storage.filestorage import FileStorage
u = TusClient(sys.argv[1]).uploader(sys.argv[2], chunk_size=65536,
    store_url=True, url_storage=FileStorage(sys.argv[3]))
u.upload(stop_at=int(sys.argv[4]) if sys.argv[4] else None)
print(json.dumps({'url': u.url, 'offset': u.offset}))
"""

        def run(stop):
            return json.loads(
                subprocess.check_output(
                    [sys.executable, "-c", child, self.base, str(fixture), str(storage), stop],
                    text=True,
                    timeout=20,
                )
            )

        first = run("65536")
        second = run("")
        self.assertEqual(first["offset"], 65536)
        self.assertEqual(first["url"], second["url"])
        self.assertEqual(second["offset"], fixture.stat().st_size)
        self.assertEqual((self.uploads / self.file_id(second["url"])).read_bytes(), fixture.read_bytes())

    def test_12_default_fingerprint_misses_tail_mutation(self):
        import io

        from tusclient.fingerprint.fingerprint import Fingerprint

        first = b"a" * 65536 + b"b" * 1024
        second = b"a" * 65536 + b"c" * 1024
        self.assertNotEqual(hashlib.sha256(first).digest(), hashlib.sha256(second).digest())
        self.assertEqual(
            Fingerprint().get_fingerprint(io.BytesIO(first)),
            Fingerprint().get_fingerprint(io.BytesIO(second)),
        )

    def test_13_kill_tusd_mid_patch_and_resume(self):
        data = bytes(range(256)) * 1024
        _, headers, _ = self.create(len(data))
        url = headers["location"]
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            sock.sendall(
                (
                    f"PATCH {urlsplit(url).path} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.port}\r\n"
                    "Tus-Resumable: 1.0.0\r\nUpload-Offset: 0\r\n"
                    "Content-Type: application/offset+octet-stream\r\n"
                    f"Content-Length: {len(data)}\r\n\r\n"
                ).encode()
                + data[:65536]
            )
            source = self.uploads / self.file_id(url)
            deadline = time.monotonic() + 3
            while source.stat().st_size == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreater(source.stat().st_size, 0)
            self.proc.kill()
            self.proc.wait(timeout=5)
        finally:
            sock.close()
            type(self).start_tusd()
        status, headers, _ = self.request("HEAD", url)
        self.assertEqual(status, 200)
        offset = int(headers["upload-offset"])
        self.assertGreater(offset, 0)
        self.assertLessEqual(offset, 65536)
        self.assertEqual(self.patch(url, data[offset:], offset)[0], 204)
        self.assertEqual(source.read_bytes(), data)

    def test_14_delete_cancels_active_patch(self):
        _, headers, _ = self.create(262144)
        url = headers["location"]
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            sock.sendall(
                (
                    f"PATCH {urlsplit(url).path} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.port}\r\n"
                    "Tus-Resumable: 1.0.0\r\nUpload-Offset: 0\r\n"
                    "Content-Type: application/offset+octet-stream\r\n"
                    "Content-Length: 262144\r\n\r\n"
                ).encode()
                + b"a" * 65536
            )
            source = self.uploads / self.file_id(url)
            deadline = time.monotonic() + 3
            while source.stat().st_size == 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreater(source.stat().st_size, 0)
            self.assertEqual(self.request("DELETE", url)[0], 204)
            self.assertEqual(self.request("HEAD", url)[0], 404)
            self.assertEqual(self.patch(url, b"x")[0], 404)
            self.assertFalse(source.exists())
        finally:
            sock.close()

    def test_15_lifecycle_missing_events_and_mismatch(self):
        from byteforge_mcp_file_friend_core.lifecycle import Lifecycle

        # Separate metadata roots, but real tusd storage. Only register the tested IDs;
        # other protocol probes intentionally lack File Friend metadata.
        store = Lifecycle(self.root, "https://example.invalid")
        for matches in (True, False):
            data = b"complete verified content"
            digest = hashlib.sha256(data if matches else b"wrong").hexdigest()
            _, headers, _ = self.create(len(data), metadata={"sha256": digest})
            url = headers["location"]
            file_id = self.file_id(url)
            self.assertEqual(self.patch(url, data)[0], 204)
            info = json.loads((self.uploads / (file_id + ".info")).read_text())
            store.register(file_id, info["MetaData"], info["Size"])
            store.verify(file_id)

            def terminate(target):
                self.assertIn(self.request("DELETE", self.base + target)[0], (204, 404))

            store.drain_delete(file_id, terminate)
            self.assertEqual(store.read(file_id)["state"], "ready" if matches else "failed")
            self.assertEqual(store.published(store.read(file_id)).exists(), matches)
            if matches:
                self.assertEqual(store.published(store.read(file_id)).read_bytes(), data)
                store.request_delete(file_id)
                store.drain_delete(file_id, terminate)
                self.assertFalse(store.published(store.read(file_id)).exists())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tusd", required=True)
    args = parser.parse_args()
    Spike.tusd = str(Path(args.tusd).resolve())
    unittest.main(argv=[sys.argv[0]], verbosity=2)
