"""Capability-based status and authenticated shared-administration deletion."""

from byteforge_mcp_file_friend_models import validate_id
from flask.views import MethodView
from flask_smorest import Blueprint, abort

from byteforge_mcp_file_friend_backend.common import require_admin, store

blp = Blueprint("files", __name__, url_prefix="/api/files", description="File lifecycle")


def existing(file_id: str) -> dict:
    try:
        validate_id(file_id)
        return store().status(file_id)
    except (ValueError, FileNotFoundError):
        abort(404, message="File not found")


@blp.route("/<string:file_id>")
class FileResource(MethodView):
    @blp.response(200, description="Lifecycle status; ready includes the file reference")
    def get(self, file_id: str) -> tuple[dict, int]:
        result = existing(file_id)
        return result, 410 if result["state"] in ("deleted", "expired") else 200

    @blp.response(202, description="Deletion recorded; worker completes storage cleanup")
    def delete(self, file_id: str) -> tuple[dict, int]:
        require_admin()
        existing(file_id)
        try:
            store().request_delete(file_id)
            result = store().status(file_id)
        except FileNotFoundError:
            abort(404, message="File not found")
        return result, 200 if result["state"] in ("deleted", "expired", "failed") else 202
