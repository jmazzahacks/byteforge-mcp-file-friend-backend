"""ByteForge Flask application factory."""

from byteforge_mcp_file_friend_core.database import DatabaseUnavailable
from byteforge_mcp_file_friend_core.lifecycle import Lifecycle
from byteforge_mcp_file_friend_models import FriendConfig
from flask import Flask, Response, request
from flask_smorest import Api, abort


def create_app(config: FriendConfig | None = None) -> Flask:
    from byteforge_mcp_file_friend_core.observability import configure_service_logging

    from byteforge_mcp_file_friend_backend.metrics import instrument

    configure_service_logging("file-friend-api")
    config = config or FriendConfig.from_env()
    config.validate()
    app = Flask(__name__)
    app.logger.handlers.clear()
    app.logger.propagate = True
    instrument(app)
    app.config.update(
        API_TITLE="File Friend", API_VERSION="v1", OPENAPI_VERSION="3.0.3", MAX_CONTENT_LENGTH=65536
    )
    app.extensions["ff_config"] = config
    app.extensions["ff_store"] = Lifecycle(config.root, config.public_url, config=config)
    api = Api(app)
    from byteforge_mcp_file_friend_backend.blueprints.files import blp as files
    from byteforge_mcp_file_friend_backend.blueprints.hooks import blp as hooks

    api.register_blueprint(files)
    api.register_blueprint(hooks)

    @app.before_request
    def reject_method_override() -> None:
        # Authorization must apply to the method actually executed.
        if any(name in request.headers for name in (
            "X-HTTP-Method-Override", "X-Method-Override", "X-HTTP-Method",
        )):
            abort(400, message="Method override headers are not supported")

    @app.errorhandler(DatabaseUnavailable)
    def database_unavailable(error):
        app.extensions["ff_database_errors"].inc()
        return {"error": "database_unavailable"}, 503, {"Retry-After": "5"}

    @app.after_request
    def no_cache(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/internal/auth")
    def auth() -> tuple[str, int]:
        from byteforge_mcp_file_friend_backend.common import require_admin

        if app.extensions["ff_config"].auth_mode != "development":
            abort(404)
        require_admin()
        return "", 204

    @app.get("/ready")
    def ready() -> tuple[dict, int]:
        from byteforge_mcp_file_friend_core.worker import healthy

        if not app.extensions["ff_store"].repository.database.healthy():
            return {"status": "database_unavailable"}, 503
        if not healthy(config.root):
            return {"status": "worker_unavailable"}, 503
        return {"status": "ready"}, 200

    return app
