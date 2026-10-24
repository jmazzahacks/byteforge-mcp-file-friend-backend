"""Backend trust boundary; actual Gatekeeper/nginx deployment is Apollo-owned."""

import base64
from dataclasses import replace

import pytest
from byteforge_mcp_file_friend_models import FriendConfig

from byteforge_mcp_file_friend_backend import create_app

SECRET = "a" * 64


@pytest.fixture
def ingress_app(tmp_path):
    return create_app(FriendConfig(
        tmp_path, "https://ff.example", hook_token="hook-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        auth_mode="ingress", ingress_shared_secret=SECRET, min_free=0,
    ))


@pytest.mark.parametrize("headers", [
    {}, {"Authorization": "Bearer admin"}, {"Authorization": f"Bearer {SECRET}"},
    {"X-FF-Ingress-Auth": "true"}, {"X-FF-Ingress-Auth": "é"},
    {"X-FF-Ingress-Auth": SECRET + ", " + SECRET},
    {"X-Forwarded-User": "admin", "X-Authenticated-User": "admin", "X-API-Key": SECRET},
    {"X-FF-Ingress-Auth": "hook-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"},
])
def test_spoofed_or_missing_ingress_authorization_denied(ingress_app, headers):
    client = ingress_app.test_client()
    assert client.get("/internal/auth", headers=headers).status_code == 404
    # Deny before looking up the ID; credentials never grant through another header.
    assert client.delete("/api/files/" + "a" * 32, headers=headers).status_code == 401


def test_ingress_shared_administration_rotation_and_public_reads(ingress_app):
    store = ingress_app.extensions["ff_store"]
    record = store.reserve({"filename": "a", "sha256": "a" * 64}, 3, 100, 0, 10)
    path = "/api/files/" + record["id"]
    client = ingress_app.test_client()
    assert client.get(path).status_code == 200
    assert client.head(path).status_code == 200
    assert client.get("/internal/auth", headers={"X-FF-Ingress-Auth": SECRET}).status_code == 404
    # No upload ownership: the newly authorized ingress secret can delete existing uploads.
    rotated = "b" * 64
    ingress_app.extensions["ff_config"] = replace(
        ingress_app.extensions["ff_config"], ingress_shared_secret=rotated,
    )
    assert client.delete(path, headers={"X-FF-Ingress-Auth": SECRET}).status_code == 401
    assert client.delete(path, headers={"X-FF-Ingress-Auth": rotated}).status_code == 202
    assert store.read(record["id"])["state"] == "deleting"


def test_hook_secret_and_ingress_secret_are_not_interchangeable(ingress_app):
    client = ingress_app.test_client()
    assert client.post("/internal/hooks", json={}, headers={"X-FF-Ingress-Auth": SECRET}).status_code == 401
    wrong_basic = base64.b64encode(f"tusd:{SECRET}".encode()).decode()
    assert client.post("/internal/hooks", json={}, headers={"Authorization": f"Basic {wrong_basic}"}).status_code == 401
    basic = base64.b64encode(b"tusd:hook-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx").decode()
    event = {"Type": "pre-create", "Event": {"Upload": {
        "Size": 3, "MetaData": {"filename": "a", "sha256": "a" * 64},
    }}}
    response = client.post("/internal/hooks", json=event, headers={"Authorization": f"Basic {basic}"})
    assert "ChangeFileInfo" in response.json


@pytest.mark.parametrize("name", ["X-HTTP-Method-Override", "X-Method-Override", "X-HTTP-Method"])
def test_method_overrides_rejected_even_with_ingress_authorization(ingress_app, name):
    client = ingress_app.test_client()
    headers = {"X-FF-Ingress-Auth": SECRET, name: "DELETE"}
    assert client.get("/api/files/" + "a" * 32, headers=headers).status_code == 400
    assert client.delete("/api/files/" + "a" * 32, headers=headers).status_code == 400


def test_development_mode_does_not_trust_ingress_headers(tmp_path):
    app = create_app(FriendConfig(tmp_path, "https://ff.example", hook_token="hook-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", dev_admin_token="dev"))
    client = app.test_client()
    assert client.get("/internal/auth", headers={"X-FF-Ingress-Auth": SECRET}).status_code == 401
    assert client.get("/internal/auth", headers={"Authorization": "Bearer dev"}).status_code == 204
