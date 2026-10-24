"""Internal authenticated tusd hooks. No hashing or tusd RPC in a hook."""

from byteforge_mcp_file_friend_core.lifecycle import CapacityError
from byteforge_mcp_file_friend_models import validate_id
from flask import request
from flask.views import MethodView
from flask_smorest import Blueprint, abort

from byteforge_mcp_file_friend_backend.common import config, require_hook, store

blp = Blueprint("hooks", __name__, url_prefix="/internal", description="Private tusd hooks")


def no_termination(file_id: str) -> None:
    """post-terminate is already called after tusd removed storage."""


def rejection(kind: str, message: str, code: int) -> dict:
    flag = "RejectTermination" if kind == "pre-terminate" else "RejectUpload"
    return {flag: True, "HTTPResponse": {"StatusCode": code, "Body": message}}


@blp.route("/hooks")
class HookResource(MethodView):
    def post(self) -> dict:
        require_hook()
        payload = request.get_json()
        if not isinstance(payload, dict):
            abort(400, message="Hook must be an object")
        kind = payload.get("Type")
        if kind not in ("pre-create", "post-create", "post-finish", "pre-terminate", "post-terminate"):
            abort(400, message="Unsupported hook type")
        try:
            event = payload["Event"]
            if not isinstance(event, dict):
                raise TypeError("Event must be an object")
            upload = event["Upload"]
            if not isinstance(upload, dict):
                raise TypeError("Upload must be an object")
            metadata = upload["MetaData"]
            if not isinstance(metadata, dict):
                raise TypeError("Metadata must be an object")
            if any(upload.get(flag) for flag in ("SizeIsDeferred", "IsPartial", "IsFinal")):
                raise ValueError("Known-length, non-concatenated uploads are required")
            if kind == "pre-create":
                settings = config()
                record = store().reserve(
                    metadata, upload["Size"], settings.max_size, settings.min_free, settings.max_active
                )
                return {
                    "ChangeFileInfo": {
                        "ID": record["id"],
                        "MetaData": {
                            "filename": record["filename"],
                            "sha256": record["sha256"],
                            "ttl": str(record["ttl"]),
                            "_ff_created_at": str(record["created_at"]),
                        },
                    }
                }
            file_id = validate_id(upload["ID"])
            store().register(file_id, metadata, upload["Size"], create=False)
            http_request = event.get("HTTPRequest", {})
            if not isinstance(http_request, dict):
                raise TypeError("HTTPRequest must be an object")
            headers = http_request.get("Header", {})
            if not isinstance(headers, dict):
                raise TypeError("Headers must be an object")
            expiry = headers.get("X-Ff-Expiry", headers.get("X-FF-Expiry", []))
            if kind == "pre-terminate" and expiry and not store().authorize_expiry(file_id):
                return rejection(kind, "Upload has recent activity", 409)
            if kind in ("pre-terminate", "post-terminate"):
                store().request_delete(file_id)
            if kind == "post-terminate":
                store().drain_delete(file_id, no_termination)
            return {}
        except FileNotFoundError:
            return rejection(kind, "Upload reservation not found", 409)
        except CapacityError as error:
            return rejection(kind, str(error), 503)
        except (ValueError, KeyError, TypeError) as error:
            if kind in ("pre-create", "pre-terminate"):
                return rejection(kind, str(error), 400)
            abort(400, message="Invalid upload event")
