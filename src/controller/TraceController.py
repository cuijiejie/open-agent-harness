"""执行轨迹查询接口

GET /api/trace?session=&kind=&status=&tool=&hours=&page=&size=&write_only=1

权限：一律按「当前登录用户 + 指定会话」查询。
  - 用户名只从会话取，不接受参数（防越权查别人）
  - 会话号必填，前端传当前会话
"""
from flask import Blueprint, Response, jsonify, request, session

from src.infra.utils.AuthUtil import login_required
from src.infra.utils.LogUtil import log

TraceController = Blueprint("trace", __name__)


def _me() -> str:
    return str(session.get("USERNAME") or "").strip()


@TraceController.route("/api/trace", methods=["GET"])
@login_required
def list_trace() -> tuple[Response, int]:
    try:
        me = _me()
        if not me:
            return jsonify({"error": "未登录"}), 401

        # 一律按「当前登录用户 + 指定会话」查询，不提供跨用户视图
        session_id: str = (str(request.args.get("session") or "").strip()
                           or str(request.args.get("current") or "").strip())
        if not session_id:
            return jsonify({"error": "缺少会话号", "need_session": True}), 400
        username: str = me

        from src.infra import TraceService

        res = TraceService.query(
            username=username,
            session_id=session_id,
            kind=str(request.args.get("kind") or "").strip(),
            status=str(request.args.get("status") or "").strip(),
            tool=str(request.args.get("tool") or "").strip(),
            hours=int(request.args.get("hours") or 0),
            page=int(request.args.get("page") or 1),
            size=int(request.args.get("size") or 100),
        )
        return jsonify({"ok": True, **res}), 200
    except Exception as e:
        log.error(f"查询轨迹失败: {e}", exc_info=True)
        return jsonify({"error": "查询失败"}), 500


@TraceController.route("/api/trace/purge", methods=["POST"])
@login_required
def purge_trace() -> tuple[Response, int]:
    """清理过期轨迹（仅管理员）。默认 90 天。"""
    try:
        me = _me()
        from src.infra.RbacService import rbac

        if not rbac.permissions(me).get("is_admin"):
            return jsonify({"error": "只有管理员可以清理轨迹"}), 403

        from src.infra import TraceService

        body = request.get_json(silent=True) or {}
        days = int(body.get("days") or 90)
        n = TraceService.purge(days)
        log.info(f"清理轨迹: admin={me}, days={days}, 删除={n}")
        return jsonify({"ok": True, "deleted": n, "days": days}), 200
    except Exception as e:
        log.error(f"清理轨迹失败: {e}", exc_info=True)
        return jsonify({"error": "清理失败"}), 500
