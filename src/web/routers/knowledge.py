#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""业务知识（长期记忆）接口

GET    /api/knowledge            我的知识 + 共享知识（管理员可见全部）
POST   /api/knowledge            新增（scope=global 需管理员）
DELETE /api/knowledge/{kid}      删除（只能删自己的；管理员可删共享）
"""
from fastapi import APIRouter, Body, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from src.infra.utils.LogUtil import log
from src.web.security import require_login

router: APIRouter = APIRouter()


def _is_admin(username: str) -> bool:
    try:
        from src.infra.RbacService import rbac

        return bool(rbac.permissions(username).get("is_admin"))
    except Exception:
        return False


@router.get("/api/knowledge")
def list_knowledge(username: str = Depends(require_login)):
    """列出：自己的个人知识 + 全部共享知识 + （管理员）所有个人知识"""
    try:
        from src.agent.memory import knowledge as K

        admin = _is_admin(username)
        if admin:
            items = K.list_items(limit=300)
        else:
            items = [r for r in K.list_items(limit=300)
                     if r["scope"] == "global" or r.get("username") == username]

        # 个人知识条数 / 上限，给前端展示
        mine = [r for r in items if r["scope"] == "user" and r.get("username") == username]
        # 必须过 jsonable_encoder：`K.list_items()` 里 created_at / expires_at 是
        # pymysql 返回的 datetime，而 Starlette 的 JSONResponse 直接 json.dumps，
        # 不走 FastAPI 的编码器 → TypeError → 整个接口 500
        #（Flask 的 jsonify 会自动序列化 datetime，这是迁移时丢掉的语义）
        return JSONResponse(jsonable_encoder({
            "ok": True,
            "items": items,
            "is_admin": admin,
            "mine_count": len(mine),
            "limit": K.MAX_ITEMS_PER_USER,
        }))
    except Exception as e:
        log.error(f"获取知识列表失败: {e}", exc_info=True)
        return JSONResponse({"error": "获取知识列表失败"}, status_code=500)


@router.post("/api/knowledge")
def add_knowledge(payload: dict | None = Body(None), username: str = Depends(require_login)):
    try:
        data = payload or {}
        content = str(data.get("content") or "").strip()
        tags = data.get("tags") or ""      # 数组/字符串都接受，统一在 knowledge._norm_tags 处理
        scope = str(data.get("scope") or "user").strip()

        if not content:
            return JSONResponse({"error": "内容不能为空"}, status_code=400)
        if scope == "global" and not _is_admin(username):
            return JSONResponse({"error": "只有管理员可以添加共享知识"}, status_code=403)

        from src.agent.memory import knowledge as K

        res = K.add(content, tags=tags, scope=scope, username=username)
        if not res.get("ok"):
            return JSONResponse({"error": res.get("error") or "保存失败"}, status_code=400)
        return JSONResponse({"ok": True, "item": res})
    except Exception as e:
        log.error(f"新增知识失败: {e}", exc_info=True)
        return JSONResponse({"error": "新增知识失败"}, status_code=500)


@router.put("/api/knowledge/{kid}")
def update_knowledge(kid: int, payload: dict | None = Body(None),
                     username: str = Depends(require_login)):
    """改一条知识的内容/关键词（个人只能改自己的，共享只有管理员能改）"""
    try:
        from src.agent.memory import knowledge as K

        row = K.get_by_id(kid)
        if not row:
            return JSONResponse({"error": "知识不存在或无权操作"}, status_code=404)
        if row["scope"] == "global" and not _is_admin(username):
            return JSONResponse({"error": "只有管理员可以修改共享知识"}, status_code=403)
        if row["scope"] == "user" and row.get("username") != username:
            return JSONResponse({"error": "知识不存在或无权操作"}, status_code=404)

        data = payload or {}
        res = K.update(kid,
                       content=data.get("content") if "content" in data else None,
                       tags=data.get("tags") if "tags" in data else None)
        if not res.get("ok"):
            return JSONResponse({"error": res.get("error") or "修改失败"}, status_code=400)
        log.warning(f"知识已修改: id={kid}, by={username}, scope={row['scope']}")
        return JSONResponse({"ok": True, "item": {"id": kid,
                                                  "content": data.get("content", row.get("content")),
                                                  "tags": data.get("tags", row.get("tags"))}})
    except Exception as e:
        log.error(f"修改知识失败: {e}", exc_info=True)
        return JSONResponse({"error": "修改知识失败"}, status_code=500)


@router.delete("/api/knowledge/{kid}")
def delete_knowledge(kid: int, username: str = Depends(require_login)):
    try:
        from src.agent.memory import knowledge as K

        # 直接按主键查（不要用 list_items(limit=500) 建索引：知识超过 500 条时，
        # 更早的个人知识不在结果里 → 用户永远删不掉）
        row = K.get_by_id(kid)
        if not row:
            return JSONResponse({"error": "知识不存在或无权操作"}, status_code=404)
        if row["scope"] == "global" and not _is_admin(username):
            return JSONResponse({"error": "只有管理员可以删除共享知识"}, status_code=403)
        if row["scope"] == "user" and row.get("username") != username:
            return JSONResponse({"error": "知识不存在或无权操作"}, status_code=404)

        res = K.delete(kid, allow_global=True)   # 归属/管理员校验已在上面做完
        return JSONResponse({"ok": True, "deleted": res.get("deleted", 0)})
    except Exception as e:
        log.error(f"删除知识失败: {e}", exc_info=True)
        return JSONResponse({"error": "删除知识失败"}, status_code=500)
