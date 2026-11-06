#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""用户偏好接口：只操作当前登录人自己的偏好"""
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from src.agent.memory import user_prefs as prefs
from src.web.security import require_login

router: APIRouter = APIRouter()


@router.get("/api/prefs")
def list_prefs(username: str = Depends(require_login)):
    rows = prefs.list_prefs(username)
    return JSONResponse({
        "items": [{"id": r["id"], "content": r["content"]} for r in rows],
        "max_items": prefs.MAX_ITEMS,
        "max_content": prefs.MAX_CONTENT,
    })


@router.post("/api/prefs")
def add_pref(payload: dict | None = Body(None), username: str = Depends(require_login)):
    content = str((payload or {}).get("content") or "")
    ok, msg = prefs.add_pref(username, content)
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 400)


@router.delete("/api/prefs/{pref_id}")
def del_pref(pref_id: int, username: str = Depends(require_login)):
    ok, msg = prefs.del_pref(username, pref_id=pref_id)
    return JSONResponse({"ok": ok, "message": msg}, status_code=200 if ok else 404)
