"""请求级中间件（纯 ASGI，不依赖 Flask）

三个职责，都是**行为契约**（切流时不能变）：
  1. `X-Request-ID`：有就沿用（网关/前端传的），没有就生成；回写到响应头，日志里带上
  2. 接口响应 `Cache-Control: no-store`：避免浏览器缓存 /api 的 JSON（切流后拿到旧数据）
  3. 请求体超过上限直接 413（返回一句人话 JSON，而不是框架默认页）

P0 不含 DB session 管理：open-agent 的数据库这阶段还是同步的，
等数据层异步化（P3）时再把 `open_request_session/close_request_session` 加回来。
"""
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from src.infra.utils.LogUtil import log

MAX_BODY_BYTES: int = 100 * 1024 * 1024 + 8 * 1024 * 1024   # 100MB + multipart 余量
_RAW_HEADERS: int = 3          # uvicorn httptools 用 3 元组 (name, value, dup)


class RequestContextMiddleware:
    """请求 ID + 访问日志 + 413"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict()
        for item in scope.get("headers") or []:
            name, value = item[0], item[1]
            headers[name.decode("latin-1").lower()] = value.decode("latin-1")

        rid: str = (headers.get("x-request-id") or "").strip() or uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = rid

        raw_len: str = headers.get("content-length") or ""
        if raw_len.isdigit() and int(raw_len) > MAX_BODY_BYTES:
            body = b'{"error":"\u4e0a\u4f20\u5185\u5bb9\u592a\u5927\uff08\u5355\u6b21\u4e0a\u4f20\u4e0a\u9650 100MB\uff09"}'
            path = scope.get("path", "")
            log.warning(f"[{rid}] 请求体过大被拒: {path} ({raw_len}B)")
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode()),
                                    (b"x-request-id", rid.encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        t0: float = time.time()
        status_holder: dict = {"status": 0}
        path: str = scope.get("path", "")
        method: str = scope.get("method", "")

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                hdrs = list(message.get("headers") or [])
                names = {h[0].lower() for h in hdrs}
                if b"x-request-id" not in names:
                    hdrs.append((b"x-request-id", rid.encode()))
                # 接口 JSON 一律禁缓存
                if path.startswith("/open-agent-harness/api/") and b"cache-control" not in names:
                    hdrs.append((b"cache-control", b"no-store"))
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            elapsed: float = time.time() - t0
            if status_holder["status"] >= 400:
                log.warning(f"[{rid}] {method} {path} → {status_holder['status']} ({elapsed:.3f}s)")
            else:
                log.info(f"[{rid}] {method} {path} → {status_holder['status']} ({elapsed:.3f}s)")
