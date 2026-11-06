#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FastAPI 侧 HTTP 层公共件：上传目录、SSE 响应头、客户端 IP 等

原来这些散落在 `src/app.py`（Flask 应用对象）上，迁到 FastAPI 后集中在这里，
避免每个路由各自 `os.path.dirname(__file__)` 算一遍路径。
"""
from __future__ import annotations

import os

from fastapi import Request

# 项目根（src/web/http.py → src/web → src → 根）
_ROOT: str = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 上传中转目录（用户文件落到这里后再进沙箱）。与 Flask 时期同一个位置：<repo>/src/uploads
UPLOAD_FOLDER: str = os.path.join(_ROOT, "src", "uploads")

# 前端构建产物目录
DIST_DIR: str = os.path.join(_ROOT, "frontend", "dist")

# 上传口径（已确认）：文件个数不限，单次总量 ≤ 100MB
TOTAL_UPLOAD_BYTES: int = 100 * 1024 * 1024

# SSE 必备响应头：X-Accel-Buffering=no 关掉反向代理缓冲，否则前端打字机效果消失
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def client_ip(request: Request) -> str:
    """客户端 IP（替代 Flask 的 request.remote_addr）

    只有**直连方是本机/回环**（即前面确实有一层本机反向代理）时才信任
    X-Forwarded-For；否则一律用 TCP 对端地址。无条件信任 XFF 会让任何人
    伪造头绕过登录限速/审计（`X-Forwarded-For: 1.2.3.4` 每次换一个）。
    """
    peer: str = getattr(request.client, "host", "") or "-"
    if peer in ("127.0.0.1", "::1", "localhost"):
        fwd: str = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip() or peer
    return peer


def wants_attachment(request: Request) -> bool:
    """`?download=1|true|yes` → 以附件下载"""
    return str(request.query_params.get("download", "")).lower() in ("1", "true", "yes")
