"""Per-application services and explicit local-development authentication."""

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
    # This first slice has local development auth. Production Gatekeeper integration
    # must replace this verifier; never trust a client-controlled forwarding header.
    token = config().dev_admin_token
    supplied = request.headers.get("Authorization", "")
    if not token or not hmac.compare_digest(supplied.encode("utf-8"), f"Bearer {token}".encode()):
        abort(401, message="A valid File Friend administrator token is required")


def require_hook() -> None:
    auth = request.authorization
    if (
        auth is None
        or auth.type != "basic"
        or auth.username != "tusd"
        or not hmac.compare_digest((auth.password or "").encode("utf-8"), config().hook_token.encode("utf-8"))
    ):
        abort(401, message="Invalid hook credentials")
