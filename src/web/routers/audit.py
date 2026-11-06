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
            limit=int(qp.get("limit") or 100),
            offset=int(qp.get("offset") or 0),
        )
        return JSONResponse({"ok": True, **res})
    except Exception as e:
        log.error(f"查询审计失败: {e}", exc_info=True)
        return JSONResponse({"error": "查询审计失败"}, status_code=500)
