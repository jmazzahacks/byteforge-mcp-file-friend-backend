import base64
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from byteforge_mcp_file_friend_models import FriendConfig

from byteforge_mcp_file_friend_backend import create_app


@pytest.fixture
def app(tmp_path: Path):
    return create_app(
        FriendConfig(
            tmp_path,
            "https://ff.example",
            hook_token="hook-secret",
            dev_admin_token="admin-secret",
            min_free=0,
            max_size=1000,
            max_active=2,
        )
    )


def hook_headers() -> dict:
    return {"Authorization": "Basic " + base64.b64encode(b"tusd:hook-secret").decode()}


def create_event(size: int = 3, **metadata: str) -> dict:
    return {
        "Type": "pre-create",
        "Event": {
            "Upload": {
                "Size": size,
                "MetaData": {"filename": "a.txt", "sha256": hashlib.sha256(b"abc").hexdigest(), **metadata},
            }
        },
    }


def test_hook_authentication_and_admission(app) -> None:
    client = app.test_client()
    assert client.post("/internal/hooks", json=create_event()).status_code == 401
    assert (
        client.post(
            "/internal/hooks", json=create_event(), headers={"Authorization": "Bearer admin-secret"}
        ).status_code
        == 401
    )
    response = client.post("/internal/hooks", json=create_event(), headers=hook_headers())
    assert response.status_code == 200
    file_id = response.json["ChangeFileInfo"]["ID"]
    assert client.get(f"/api/files/{file_id}").json == {"id": file_id, "state": "uploading"}
    assert client.get(f"/api/files/{file_id}").headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "metadata",
    [
        {"filename": "../escape"},
        {"filename": "dir\\escape"},
        {"filename": "bad\nname"},
        {"sha256": "wrong"},
        {"ttl": "0"},
        {"ttl": "604801"},
        {"ttl": "1.5"},
    ],
)
def test_invalid_metadata_rejected(app, metadata: dict) -> None:
    response = app.test_client().post(
        "/internal/hooks", json=create_event(**metadata), headers=hook_headers()
    )
    assert response.status_code == 200  # A tus hook rejection is a successful hook response.
    assert response.json["RejectUpload"] is True
    assert response.json["HTTPResponse"]["StatusCode"] == 400
    assert list((app.extensions["ff_config"].root / "meta").glob("*.json")) == []


def test_unknown_length_and_limits(app) -> None:
    client = app.test_client()
    event = create_event()
    event["Event"]["Upload"]["SizeIsDeferred"] = True
    assert client.post("/internal/hooks", json=event, headers=hook_headers()).json["RejectUpload"]
    assert client.post("/internal/hooks", json=create_event(1001), headers=hook_headers()).json[
        "RejectUpload"
    ]
    for _ in range(2):
        assert (
            "ChangeFileInfo"
            in client.post("/internal/hooks", json=create_event(), headers=hook_headers()).json
        )
    assert (
        client.post("/internal/hooks", json=create_event(), headers=hook_headers()).json["HTTPResponse"][
            "StatusCode"
        ]
        == 503
    )


def test_shared_admin_delete_and_token_rotation(app) -> None:
    client = app.test_client()
    store = app.extensions["ff_store"]
    file_id = client.post("/internal/hooks", json=create_event(), headers=hook_headers()).json[
        "ChangeFileInfo"
    ]["ID"]
    store.source(file_id).write_bytes(b"abc")
    store.verify(file_id)
    assert client.delete(f"/api/files/{file_id}").status_code == 401
    assert store.published(store.read(file_id)).exists()
    # Ownership is not bound to the uploader token: another accepted admin can delete.
    app.extensions["ff_config"] = replace(app.extensions["ff_config"], dev_admin_token="rotated-admin")
    response = client.delete(f"/api/files/{file_id}", headers={"Authorization": "Bearer rotated-admin"})
    assert response.status_code == 202
    assert response.json["state"] == "deleting"
    assert not store.published(store.read(file_id)).exists()
    assert (
        client.delete(f"/api/files/{file_id}", headers={"Authorization": "Bearer admin-secret"}).status_code
        == 401
    )


def test_missing_and_terminal_status(app) -> None:
    client = app.test_client()
    assert client.get("/api/files/not-an-id").status_code == 404
    assert client.get("/api/files/" + "a" * 32).status_code == 404
    file_id = client.post("/internal/hooks", json=create_event(), headers=hook_headers()).json[
        "ChangeFileInfo"
    ]["ID"]
    store = app.extensions["ff_store"]
    store.request_delete(file_id)

    def already_gone(target: str) -> None:
        pass

    store.drain_delete(file_id, already_gone)
    assert client.get(f"/api/files/{file_id}").status_code == 410
    assert client.get("/health").json == {"status": "ok"}
    assert client.get("/swagger").status_code == 404


def test_hook_secret_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="FF_HOOK_TOKEN"):
        create_app(FriendConfig(tmp_path, "https://ff.example"))


def test_non_ascii_credentials_are_rejected_without_server_error(app):
    client = app.test_client()
    assert client.get("/internal/auth", headers={"Authorization": "Bearer café"}).status_code == 401
    credentials = base64.b64encode("tusd:café".encode()).decode()
    assert (
        client.post(
            "/internal/hooks", json=create_event(), headers={"Authorization": "Basic " + credentials}
        ).status_code
        == 401
    )


def test_malformed_hook_headers_are_rejected(app):
    client = app.test_client()
    created = client.post("/internal/hooks", json=create_event(), headers=hook_headers()).json
    event = create_event()
    event["Type"] = "pre-terminate"
    event["Event"]["Upload"]["ID"] = created["ChangeFileInfo"]["ID"]
    event["Event"]["HTTPRequest"] = {"Header": None}
    response = client.post("/internal/hooks", json=event, headers=hook_headers())
    assert response.status_code == 200
    assert response.json["RejectTermination"] is True
    assert response.json["HTTPResponse"]["StatusCode"] == 400


def test_readiness_handles_malformed_heartbeat(app):
    root = app.extensions["ff_config"].root
    (root / "worker.json").write_text('{"state":"running","heartbeat":null,"failures":0}')
    assert app.test_client().get("/ready").status_code == 503


def test_public_url_rejects_paths_and_invalid_ports(tmp_path):
    for url in (
        "https://ff.example/prefix",
        "https://ff.example:0",
        "https://ff.example:abc",
        "https://ff.example\n",
    ):
        with pytest.raises(ValueError):
            FriendConfig(tmp_path, url, hook_token="hook").validate()


def test_hook_enforces_configured_storage_budget(app) -> None:
    app.extensions["ff_config"] = replace(app.extensions["ff_config"], storage_budget=3)
    client = app.test_client()
    first = client.post("/internal/hooks", json=create_event(), headers=hook_headers())
    assert "ChangeFileInfo" in first.json
    rejected = client.post("/internal/hooks", json=create_event(1), headers=hook_headers())
    assert rejected.json["RejectUpload"] is True
    assert rejected.json["HTTPResponse"]["StatusCode"] == 503
