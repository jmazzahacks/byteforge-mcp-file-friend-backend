"""Per-application services and mutually exclusive ingress/development authentication."""

import hmac

from byteforge_mcp_file_friend_core.lifecycle import Lifecycle
from byteforge_mcp_file_friend_models import FriendConfig
from flask import current_app, request
from flask_smorest import abort


def config() -> FriendConfig:
    return current_app.extensions["ff_config"]


def store() -> Lifecycle:
    return current_app.extensions["ff_store"]


def require_admin() -> None:
    settings = config()
    if settings.auth_mode == "ingress":
        # The private ingress overwrites this header ONLY after Gatekeeper allows
        # the original method/route. Client identity headers are never authority.
        expected = settings.ingress_shared_secret
        supplied = request.headers.get("X-FF-Ingress-Auth", "")
    elif settings.auth_mode == "development":
        expected = f"Bearer {settings.dev_admin_token}" if settings.dev_admin_token else ""
        supplied = request.headers.get("Authorization", "")
    else:
        abort(401, message="File Friend administrator authorization required")
    if not expected or not hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")):
        abort(401, message="File Friend administrator authorization required")


def require_hook() -> None:
    auth = request.authorization
    if (
        auth is None
        or auth.type != "basic"
        or auth.username != "tusd"
        or not hmac.compare_digest((auth.password or "").encode("utf-8"), config().hook_token.encode("utf-8"))
    ):
        abort(401, message="Invalid hook credentials")
