"""Characterize pinned tusd logging; synthetic secrets only, no production calls."""

import os
import socket
import subprocess
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.mark.parametrize("failure", ["refused", "timeout", "http500"])
def test_quiet_tusd_hook_failures_redact_password_but_log_capabilities(tmp_path, failure):
    binary = os.environ.get("TUSD_BIN")
    if not binary:
        pytest.skip("Set TUSD_BIN for pinned tusd logging probes")
    assert "Version: v2.10.1\n" in subprocess.check_output([binary, "-version"], text=True)
    token = "probe_only_" + "x" * 32

    class Hook(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            if failure == "timeout":
                time.sleep(0.5)
                return
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"synthetic hook failure")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
    hook_port = server.server_port
    thread = None
    if failure == "refused":
        server.server_close()
    else:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log_path = tmp_path / "tusd.log"
    capability = "a" * 32

    def send(method, path):
        conn = HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            conn.request(method, path, headers={"Tus-Resumable": "1.0.0", "Upload-Length": "3"})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    try:
        with log_path.open("w") as log:
            process = subprocess.Popen([
                binary, "-host=127.0.0.1", f"-port={port}", f"-upload-dir={tmp_path}",
                f"-hooks-http=http://tusd:{token}@127.0.0.1:{hook_port}/internal/hooks",
                "-hooks-enabled-events=pre-create", "-hooks-http-retry=0",
                "-hooks-http-timeout=100ms", "-verbose=false", "-show-startup-logs=false",
                "-show-greeting=false",
            ], stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        send("OPTIONS", "/files/")
                        break
                    except OSError:
                        time.sleep(0.02)
                status, body = send("POST", "/files/")
                assert status == 500
                assert token.encode() not in body
                send("HEAD", "/files/" + capability)
            finally:
                process.terminate()
                process.wait(5)
        output = log_path.read_text()
        assert "HookInvocationError" in output
        assert token not in output
        # verbose=false leaves INFO request paths enabled: raw logs are unsafe.
        assert capability in output
    finally:
        if thread:
            server.shutdown()
            server.server_close()
            thread.join(2)
