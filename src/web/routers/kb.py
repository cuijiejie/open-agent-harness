#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库（文档 RAG）接口

GET    /api/kb/docs              我的 + 全局文档列表
POST   /api/kb/docs              上传（multipart: file, scope=user|global）
DELETE /api/kb/docs/{doc_id}     删除（本人 / 管理员）
POST   /api/kb/docs/{doc_id}/reindex   重建索引
GET    /api/kb/search?q=&top_k=  检索测试（前端"知识库"页的搜索框）
GET    /api/kb/stats             文档数 / chunk 数 / 队列积压

权限口径与 pa_knowledge 一致：
    user    本人可读可写可删
    global  全员可读，只有管理员能传/删
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from src.infra.utils.LogUtil import log
from src.web.security import require_login

router: APIRouter = APIRouter()

MAX_FILE_BYTES: int = 20 * 1024 * 1024


def _is_admin(username: str) -> bool:
    try:
        from src.infra.RbacService import rbac

        return bool(rbac.permissions(username).get("is_admin"))
    except Exception:
        return False


def _enabled() -> bool:
    from config.Configs import cfg

    return bool(getattr(cfg, "kbEnabled", True))


@router.get("/api/kb/stats")
def kb_stats(username: str = Depends(require_login)):
    if not _enabled():
        return JSONResponse({"ok": False, "error": "知识库未启用"}, status_code=404)
    from src.infra.kb import embedding as embed_cli
    from src.infra.kb import store

    try:
        s = store.stats(username)          # 只统计调用者能看到的文档；docs_total 是全库
        return JSONResponse({"ok": True, **s, "embed": embed_cli.health()})
    except Exception as e:
        log.error(f"知识库统计失败: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": "读取失败（检查 PG 连接与扩展）"}, status_code=500)


@router.get("/api/kb/docs")
def list_docs(request: Request, username: str = Depends(require_login)):
    if not _enabled():
        return JSONResponse({"ok": False, "error": "知识库未启用"}, status_code=404)
    from src.infra.kb import store

    try:
        limit = int(request.query_params.get("limit") or 100)
    except ValueError:
        limit = 100
    try:
        items = store.list_docs("user", username, limit=limit)
        return JSONResponse(jsonable_encoder({"ok": True, "items": items,
                                                "is_admin": _is_admin(username)}))
    except Exception as e:
        log.error(f"知识库列表失败: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": "读取失败"}, status_code=500)


@router.post("/api/kb/docs")
async def upload_doc(file: UploadFile = File(...), scope: str = Form("user"),
                     username: str = Depends(require_login)):
    if not _enabled():
        return JSONResponse({"ok": False, "error": "知识库未启用"}, status_code=404)
    from src.infra.kb import ingest

    sc = (scope or "user").strip().lower()
    if sc not in ("user", "global"):
        return JSONResponse({"ok": False, "error": "scope 只能是 user 或 global"}, status_code=400)
    if sc == "global" and not _is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以上传共享文档"}, status_code=403)

    try:
        data = await file.read()
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"读取上传失败：{e}"}, status_code=400)
    if len(data) > MAX_FILE_BYTES:
        return JSONResponse({"ok": False, "error": "文件超过 20MB"}, status_code=413)

    try:
        res = ingest.save_upload(sc, username, file.filename or "unnamed",
                                 data, file.content_type or "", username)
    except Exception as e:
        log.error(f"知识库上传失败: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": "上传失败"}, status_code=500)

    # 落库只是登记，真正解析/切分/嵌入在独立进程里做；
    # 这里立刻踢一脚，用户不用等定时任务（页面自己轮询状态）
    if res.get("ok"):
        try:
            from src.infra.kb import kick

            kick.kick(f"upload doc={res.get('doc_id')}")
        except Exception as e:
            log.warning(f"索引触发异常（不影响上传，可手动重建）: {e}")
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@router.delete("/api/kb/docs/{doc_id}")
def delete_doc(doc_id: int, username: str = Depends(require_login)):
    if not _enabled():
        return JSONResponse({"ok": False, "error": "知识库未启用"}, status_code=404)
    from src.infra.kb import store

    doc = store.get_doc(doc_id)
    if not doc:
        return JSONResponse({"ok": False, "error": "文档不存在"}, status_code=404)
    if doc["scope"] == "global" and not _is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以删除共享文档"}, status_code=403)
    if doc["scope"] == "user" and doc["owner"] != username:
        return JSONResponse({"ok": False, "error": "无权删除他人文档"}, status_code=403)
    ok = store.delete_doc(doc_id)
    log.warning(f"知识库删除文档: doc={doc_id} by={username} ok={ok}")
    return JSONResponse({"ok": ok})


@router.post("/api/kb/docs/{doc_id}/reindex")
def reindex(doc_id: int, username: str = Depends(require_login)):
    if not _enabled():
        return JSONResponse({"ok": False, "error": "知识库未启用"}, status_code=404)
    from src.infra.kb import store

    doc = store.get_doc(doc_id)
    if not doc:
        return JSONResponse({"ok": False, "error": "文档不存在"}, status_code=404)
    if doc["scope"] == "global" and not _is_admin(username):
        return JSONResponse({"ok": False, "error": "只有管理员可以重建共享文档"}, status_code=403)
    if doc["scope"] == "user" and doc["owner"] != username:
        return JSONResponse({"ok": False, "error": "无权操作他人文档"}, status_code=403)
    task_id = store.add_task(doc_id, "reindex")
    store.set_doc_status(doc_id, "pending", error="")
    try:
        from src.infra.kb import kick

        kick.kick(f"reindex doc={doc_id}")
    except Exception as e:
        log.warning(f"索引触发异常（任务已入队，可再点重建）: {e}")
    return JSONResponse({"ok": True, "doc_id": doc_id, "task_id": task_id})


@router.get("/api/kb/search")
def kb_search(request: Request, username: str = Depends(require_login)):
    """检索测试接口（前端"知识库"页用；Agent 走 kb_search 工具）"""
    if not _enabled():
        return JSONResponse({"ok": False, "error": "知识库未启用"}, status_code=404)
    from src.infra.kb import retrieve as kb_search_mod

    q = str(request.query_params.get("q") or "").strip()
    if not q:
        return JSONResponse({"ok": False, "error": "q 不能为空"}, status_code=400)
    try:
        top_k = max(1, min(20, int(request.query_params.get("top_k") or 5)))
    except ValueError:
        top_k = 5
    try:
        items = kb_search_mod.search(q, username, top_k=top_k)
        return JSONResponse({"ok": True, "items": items, "count": len(items)})
    except Exception as e:
        log.error(f"知识库检索失败: {e}", exc_info=True)
        return JSONResponse({"ok": False, "error": "检索失败（嵌入服务是否在跑？）"}, status_code=500)
