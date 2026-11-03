"""业务知识（长期记忆）接口

GET    /api/knowledge            我的知识 + 共享知识（管理员可见全部）
POST   /api/knowledge            新增（scope=global 需管理员）
DELETE /api/knowledge/<id>       删除（只能删自己的；管理员可删共享）
"""
import json

from flask import Blueprint, Response, jsonify, request

from src.infra.utils.AuthUtil import login_required
from src.infra.utils.LogUtil import log

KnowledgeController = Blueprint("knowledge", __name__)


def _get_username() -> str:
    from flask import session

    return str(session.get("CAS_USERNAME") or "").strip()


def _is_admin(username: str) -> bool:
    try:
        from src.infra.RbacService import rbac

        return bool(rbac.permissions(username).get("is_admin"))
    except Exception:
        return False


@KnowledgeController.route("/api/knowledge", methods=["GET"])
@login_required
def list_knowledge() -> tuple[Response, int]:
    """列出：自己的个人知识 + 全部共享知识 + （管理员）所有个人知识"""
    try:
        username = _get_username()
        if not username:
            return jsonify({"error": "未登录"}), 401

        from src.agent.memory import knowledge as K

        admin = _is_admin(username)
        if admin:
            items = K.list_items(limit=300)
        else:
            items = [r for r in K.list_items(limit=300)
                     if r["scope"] == "global" or r.get("username") == username]

        # 个人知识条数 / 上限，给前端展示
        mine = [r for r in items if r["scope"] == "user" and r.get("username") == username]
        return jsonify({
            "ok": True,
            "items": items,
            "is_admin": admin,
            "mine_count": len(mine),
            "limit": K.MAX_ITEMS_PER_USER,
        }), 200
    except Exception as e:
        log.error(f"获取知识列表失败: {e}", exc_info=True)
        return jsonify({"error": "获取知识列表失败"}), 500


@KnowledgeController.route("/api/knowledge", methods=["POST"])
@login_required
def add_knowledge() -> tuple[Response, int]:
    try:
        username = _get_username()
        if not username:
            return jsonify({"error": "未登录"}), 401

        data = request.get_json(silent=True) or {}
        content = str(data.get("content") or "").strip()
        tags = str(data.get("tags") or "").strip()
        scope = str(data.get("scope") or "user").strip()

        if not content:
            return jsonify({"error": "内容不能为空"}), 400
        if scope == "global" and not _is_admin(username):
            return jsonify({"error": "只有管理员可以添加共享知识"}), 403

        from src.agent.memory import knowledge as K

        res = K.add(content, tags=tags, scope=scope, username=username)
        if not res.get("ok"):
            return jsonify({"error": res.get("error") or "保存失败"}), 400
        return jsonify({"ok": True, "item": res}), 200
    except Exception as e:
        log.error(f"新增知识失败: {e}", exc_info=True)
        return jsonify({"error": "新增知识失败"}), 500


@KnowledgeController.route("/api/knowledge/<int:kid>", methods=["DELETE"])
@login_required
def delete_knowledge(kid: int) -> tuple[Response, int]:
    try:
        username = _get_username()
        if not username:
            return jsonify({"error": "未登录"}), 401

        from src.agent.memory import knowledge as K

        # 直接按主键查（不要用 list_items(limit=500) 建索引：知识超过 500 条时，
        # 更早的个人知识不在结果里 → 用户永远删不掉）
        row = K.get_by_id(kid)
        if not row:
            return jsonify({"error": "知识不存在或无权操作"}), 404
        if row["scope"] == "global" and not _is_admin(username):
            return jsonify({"error": "只有管理员可以删除共享知识"}), 403
        if row["scope"] == "user" and row.get("username") != username:
            return jsonify({"error": "知识不存在或无权操作"}), 404

        res = K.delete(kid, allow_global=True)   # 归属/管理员校验已在上面做完
        return jsonify({"ok": True, "deleted": res.get("deleted", 0)}), 200
    except Exception as e:
        log.error(f"删除知识失败: {e}", exc_info=True)
        return jsonify({"error": "删除知识失败"}), 500
