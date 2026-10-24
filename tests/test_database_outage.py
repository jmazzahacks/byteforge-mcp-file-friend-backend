from byteforge_mcp_file_friend_models import FriendConfig

from byteforge_mcp_file_friend_backend import create_app


def test_database_unavailable_fails_closed(tmp_path):
    settings = FriendConfig(tmp_path, "https://ff.example", hook_token="hook", dev_admin_token="admin")
    app = create_app(settings)
    database = app.extensions["ff_store"].repository.database
    database.options["port"] = 1
    client = app.test_client()
    assert client.get("/api/files/" + "a" * 32).status_code == 503
    assert (
        client.delete("/api/files/" + "a" * 32, headers={"Authorization": "Bearer admin"}).status_code == 503
    )
    assert client.get("/ready").status_code == 503
    assert client.get("/ready").json["status"] == "database_unavailable"
    assert client.get("/health").status_code == 200
    assert not (tmp_path / "meta").exists()


def test_pgcat_pool_error_returns_retryable_status_without_payload_leak(tmp_path, monkeypatch):
    settings = FriendConfig(tmp_path, "https://ff.example", hook_token="hook", dev_admin_token="admin")
    app = create_app(settings)
    repository = app.extensions["ff_store"].repository
    attempts = []

    def unavailable(file_id):
        attempts.append(file_id)
        with repository.database.cursor() as cursor:
            cursor.execute("DO $$ BEGIN RAISE EXCEPTION USING ERRCODE='58000', "
                           "MESSAGE='could not get connection from the pool: AllServersDown'; END $$")

    monkeypatch.setattr(repository, "read", unavailable)
    response = app.test_client().get("/api/files/" + "a" * 32)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json == {"error": "database_unavailable"}
    assert attempts == ["a" * 32]
    assert repository.database.healthy()
