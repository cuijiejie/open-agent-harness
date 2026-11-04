"""操作审计查询接口（仅管理员）

GET /api/audit?user=&tool=&result=&start=&end=&limit=&offset=
"""
from flask import Blueprint, Response, jsonify, request

from src.infra.utils.AuthUtil import login_required
from src.infra.utils.LogUtil import log

AuditController = Blueprint("audit", __name__)


def _get_username() -> str:
    from flask import session

    return str(session.get("USERNAME") or "").strip()


@AuditController.route("/api/audit", methods=["GET"])
@login_required
def list_audit() -> tuple[Response, int]:
    try:
        username = _get_username()
        if not username:
            return jsonify({"error": "未登录"}), 401

        from src.infra.RbacService import rbac

        if not rbac.permissions(username).get("is_admin"):
            return jsonify({"error": "只有管理员可以查看审计"}), 403

        from src.infra import AuditService

        res = AuditService.query(
            username=str(request.args.get("user") or "").strip(),
            tool=str(request.args.get("tool") or "").strip(),
            result=str(request.args.get("result") or "").strip(),
            start=str(request.args.get("start") or "").strip(),
            end=str(request.args.get("end") or "").strip(),
            limit=int(request.args.get("limit") or 100),
            offset=int(request.args.get("offset") or 0),
        )
        return jsonify({"ok": True, **res}), 200
    except Exception as e:
        log.error(f"查询审计失败: {e}", exc_info=True)
        return jsonify({"error": "查询审计失败"}), 500
