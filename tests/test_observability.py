import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from byteforge_mcp_file_friend_models import FriendConfig

from byteforge_mcp_file_friend_backend import create_app


def test_metrics_are_bounded_and_logging_drops_capabilities(tmp_path, capsys):
    app = create_app(FriendConfig(tmp_path, "https://ff.example", hook_token="a" * 32))
    client = app.test_client()
    secret = "PRIVATE_CAPABILITY_SENTINEL"
    client.get("/api/files/" + secret, headers={"Authorization": "Bearer " + secret})
    client.open("/" + secret, method=secret)
    try:
        raise RuntimeError(secret)
    except RuntimeError:
        app.logger.exception("request failed %s", secret, extra={"token": secret})
    metrics = client.get("/metrics").text
    assert secret not in metrics
    assert 'method="OTHER"' in metrics
    assert 'route="files"' in metrics
    assert secret not in capsys.readouterr().out
    assert "ff_http_requests_total" in metrics
    # Factory re-entry must not silently lose counters to duplicate registry handling.
    second = create_app(FriendConfig(tmp_path / "second", "https://ff.example", hook_token="a" * 32))
    second.test_client().get("/health")
    assert 'route="health",status="200"} 1.0' in second.test_client().get("/metrics").text


def test_loki_queue_receives_only_sanitized_records(monkeypatch):
    import byteforge_loki_logging.logging_config as loki
    from byteforge_mcp_file_friend_core.observability import configure_service_logging, event

    payloads = []

    class Response:
        status_code = 204
        text = ""

        def raise_for_status(self):
            pass

    def post(session, url, **kwargs):
        payloads.append(json.dumps(kwargs))
        return Response()

    monkeypatch.setenv("DEBUG_LOCAL", "false")
    monkeypatch.setenv("LOKI_ENDPOINT", "https://loki.example/loki/api/v1/push")
    monkeypatch.setenv("LOKI_USER", "fake-user")
    monkeypatch.setenv("LOKI_PASSWORD", "fake-password")
    monkeypatch.setenv("LOKI_CA_BUNDLE_PATH", "/etc/ssl/certs/ca-certificates.crt")
    def connected(*args):
        return True, ""

    monkeypatch.setattr(loki, "_test_loki_connection", connected)
    monkeypatch.setattr(loki.requests.Session, "post", post)
    configure_service_logging("file-friend-api")
    event("request_completed", method="GET", route="files", status=200, token="PRIVATE_SENTINEL")
    logging.getLogger("dependency").error("PRIVATE_SENTINEL", extra={"url": "PRIVATE_SENTINEL"})
    assert loki.flush_logging(timeout=3)
    assert payloads
    joined = "".join(payloads)
    assert "PRIVATE_SENTINEL" not in joined
    assert "request_completed" in joined
    assert "file-friend-api" in joined


def test_gunicorn_counters_stay_aggregated_across_workers_and_restart(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    shards = tmp_path / "shards"
    shards.mkdir()
    env = dict(os.environ, DEBUG_LOCAL="true", FF_DATA_DIR=str(tmp_path / "data"),
               FF_HOOK_TOKEN="h" * 32, PROMETHEUS_MULTIPROC_DIR=str(shards))
    log_path = tmp_path / "gunicorn.log"

    def fetch(path):
        with urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
            return response.read().decode()

    def count():
        for line in fetch("/metrics").splitlines():
            if line.startswith("ff_http_requests_total{") and 'route="health"' in line:
                return float(line.rsplit(" ", 1)[1])
        return 0

    with log_path.open("w") as log:
        process = subprocess.Popen([sys.executable, "-m", "gunicorn", "-c", "gunicorn_conf.py",
                                    "--bind", f"127.0.0.1:{port}", "--workers", "3",
                                    "byteforge_mcp_file_friend_backend:create_app()"],
                                   env=env, stdout=log, stderr=log)
        try:
            for _ in range(150):
                try:
                    fetch("/metrics")
                    break
                except OSError:
                    if process.poll() is not None:
                        raise AssertionError(log_path.read_text())
                    time.sleep(0.05)
            for _ in range(20):
                fetch("/health")
            values = [count() for _ in range(10)]
            assert values == [20.0] * 10
            children_file = Path(f"/proc/{process.pid}/task/{process.pid}/children")
            children = children_file.read_text().split()
            assert len(children) == 3
            os.kill(int(children[0]), signal.SIGKILL)
            for _ in range(100):
                current = children_file.read_text().split()
                if len(current) == 3 and children[0] not in current:
                    break
                time.sleep(0.05)
            assert count() == 20
            fetch("/health")
            assert [count() for _ in range(5)] == [21.0] * 5
        finally:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def test_loki_unavailable_falls_back_to_sanitized_stdout(monkeypatch, capsys):
    import byteforge_loki_logging.logging_config as loki
    from byteforge_mcp_file_friend_core.observability import configure_service_logging, event

    def unavailable(*args):
        return False, "synthetic unavailable"

    monkeypatch.setenv("DEBUG_LOCAL", "false")
    monkeypatch.setenv("LOKI_ENDPOINT", "https://loki.example/loki/api/v1/push")
    monkeypatch.setenv("LOKI_USER", "fake-user")
    monkeypatch.setenv("LOKI_PASSWORD", "fake-password")
    monkeypatch.setenv("LOKI_CA_BUNDLE_PATH", "/etc/ssl/certs/ca-certificates.crt")
    monkeypatch.setattr(loki, "_test_loki_connection", unavailable)
    configure_service_logging("file-friend-api")
    event("request_completed", token="PRIVATE_SENTINEL")
    logging.getLogger("dependency").error("PRIVATE_SENTINEL")
    output = capsys.readouterr()
    assert "PRIVATE_SENTINEL" not in output.out + output.err
    assert "request_completed" in output.out
    assert "Falling back to stdout" in output.err
