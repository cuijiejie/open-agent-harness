#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""操作审计查询接口（仅管理员）

GET /api/audit?user=&tool=&result=&start=&end=&limit=&offset=
"""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from src.infra.utils.LogUtil import log
from src.web.security import require_login

router: APIRouter = APIRouter()


def _int(raw, default: int, lo: int, hi: int) -> int:
    """宽容解析整数：非法/空用默认值，结果夹到 [lo, hi]（避免 500 与大查询）"""
    try:
        v: int = int(str(raw).strip()) if raw is not None and str(raw).strip() else default
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


@router.get("/api/audit")
def list_audit(request: Request, username: str = Depends(require_login)):
    try:
        from src.infra.RbacService import rbac

        if not rbac.permissions(username).get("is_admin"):
            return JSONResponse({"error": "只有管理员可以查看审计"}, status_code=403)

        from src.infra import AuditService

        qp = request.query_params
        res = AuditService.query(
            username=str(qp.get("user") or "").strip(),
            tool=str(qp.get("tool") or "").strip(),
            result=str(qp.get("result") or "").strip(),
            start=str(qp.get("start") or "").strip(),
            end=str(qp.get("end") or "").strip(),
            limit=_int(qp.get("limit"), 100, 1, 1000),
            offset=_int(qp.get("offset"), 0, 0, 10_000_000),
        )
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        log.error(f"查询审计失败: {e}", exc_info=True)
        return JSONResponse({"error": "查询审计失败"}, status_code=500)
