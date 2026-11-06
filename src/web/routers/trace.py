#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""执行轨迹查询接口

GET  /api/trace?session=&kind=&status=&tool=&hours=&page=&size=&write_only=1
POST /api/trace/purge   清理过期轨迹（仅管理员）

权限：一律按「当前登录用户 + 指定会话」查询。
  - 用户名只从会话取，不接受参数（防越权查别人）
  - 会话号必填，前端传当前会话
"""
from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from src.infra.utils.LogUtil import log
from src.web.security import require_login

router: APIRouter = APIRouter()


def _int(raw, default: int, lo: int, hi: int) -> int:
    try:
        v: int = int(str(raw).strip()) if raw is not None and str(raw).strip() else default
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


@router.get("/api/trace")
def list_trace(request: Request, username: str = Depends(require_login)):
    try:
        # 一律按「当前登录用户 + 指定会话」查询，不提供跨用户视图
        qp = request.query_params
        session_id: str = (str(qp.get("session") or "").strip()
                           or str(qp.get("current") or "").strip())
        if not session_id:
            return JSONResponse({"error": "缺少会话号", "need_session": True}, status_code=400)

        from src.infra import TraceService

        res = TraceService.query(
            username=username,
            session_id=session_id,
            kind=str(qp.get("kind") or "").strip(),
            status=str(qp.get("status") or "").strip(),
            tool=str(qp.get("tool") or "").strip(),
            hours=_int(qp.get("hours"), 0, 0, 24 * 365),
            page=_int(qp.get("page"), 1, 1, 1_000_000),
            size=_int(qp.get("size"), 100, 1, 500),
        )
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        log.error(f"查询轨迹失败: {e}", exc_info=True)
        return JSONResponse({"error": "查询失败"}, status_code=500)


@router.post("/api/trace/purge")
def purge_trace(payload: dict | None = Body(None), username: str = Depends(require_login)):
    """清理过期轨迹（仅管理员）。默认 90 天。"""
    try:
        from src.infra.RbacService import rbac

        if not rbac.permissions(username).get("is_admin"):
            return JSONResponse({"error": "只有管理员可以清理轨迹"}, status_code=403)

        from src.infra import TraceService

        days = _int((payload or {}).get("days"), 90, 1, 3650)
        n = TraceService.purge(days)
        log.info(f"清理轨迹: admin={username}, days={days}, 删除={n}")
        return JSONResponse({"ok": True, "deleted": n, "days": days})
    except Exception as e:
        log.error(f"清理轨迹失败: {e}", exc_info=True)
        return JSONResponse({"error": "清理失败"}, status_code=500)
