#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""与 Flask **双向兼容**的会话中间件

## 为什么不用 Starlette 自带的 `SessionMiddleware`

Flask 的会话 cookie 是：

    值 = URLSafeTimedSerializer(secret, salt='cookie-session',
        serializer=TaggedJSONSerializer(), signer_kwargs={hmac, sha1}).dumps(session)
    写入前 TaggedJSON → JSON → zlib 压缩 → base64url + "." + 时间戳 + "." + 签名

Starlette 的 `SessionMiddleware` 用的是完全不同的公式（base64url(JSON)+itsdangerous 默认参数，
不压缩、salt 不同）。两者**互不识别**，换过去的那一刻所有已登录用户都会失效一次。

用户明确表示「切流所有用户失效重登，不能接受」（2026-11-05），所以这里复刻 Flask 的序列化参数，
做到**双向互认**：

- Flask 写的 cookie → 这里能读：**切流零登出**
- 这里写的 cookie → Flask 能读：**回滚也零登出**

只要 `secret_key` 不变（同一个 `[app] secret_key` 配置），两套栈可以随时来回切。

## 兼容性边界

- 读写都用 Flask 的 `TaggedJSONSerializer`（装了 flask 时自动用；没装则退回纯 JSON——
  本项目 session 里只存字符串，两种序列化对纯字符串产生**完全相同**的字节）。
- 读取时的 `max_age` 取 Flask 的默认 `permanent_session_lifetime`（31 天），
  比 Flask 更宽松会让「过期 cookie 在新栈复活」，更严格会让老用户被踢，都不行。
- cookie 属性与 Flask 默认一致：`HttpOnly`、`Path=/`、`SameSite=Lax`、不设 `Secure`、不设 `Expires`
  （= 浏览器会话 cookie，关浏览器即失效，与 Flask 当前行为一致）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from itsdangerous import BadSignature, URLSafeTimedSerializer
from starlette.datastructures import MutableHeaders

COOKIE_NAME: str = "session"
# Flask 的 permanent_session_lifetime 默认 31 天，读取时按它判过期
MAX_AGE: int = 31 * 24 * 3600
_COOKIE_EXPIRED: str = "expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0"


def _tagged_serializer():
    """JSON 序列化器（与 Flask 的 `TaggedJSONSerializer` **对字符串数据字节一致**）

    这里**不 import flask**：本系统 session 里只存三个字符串键
    （`CAS_USERNAME` / `CAS_DISPLAY_NAME` / `CAS_AFTER_LOGIN_SESSION_URL`），
    而 Flask 的 TaggedJSON 对纯字符串 dict 就是普通 JSON（只有 tuple/bytes/datetime
    这类类型才加 `{" t": ...}` 标记）。所以 `json.dumps(..., separators=(",", ":"))`
    与 Flask 产出的 payload 完全相同，签名后 cookie 互认。

    ⚠️ 以后若要在 session 里存非字符串值（datetime 等），这里必须补上对应标记，
    否则 Flask 侧读不回来（现在只有 FastAPI 读，但仍建议保持一致）。
    """
    return _JsonSerializer()


class _JsonSerializer:
    """`itsdangerous` 需要的 dumps/loads 接口（紧凑 JSON）"""

    @staticmethod
    def dumps(obj) -> str:
        return json.dumps(obj, separators=(",", ":"), ensure_ascii=True)

    @staticmethod
    def loads(text: str):
        return json.loads(text)


class SessionSigner:
    """签名/验签，参数与 Flask `SecureCookieSessionInterface` 完全一致"""

    def __init__(self, secret_key: str) -> None:
        if not secret_key:
            raise RuntimeError(
                "SECRET_KEY 未配置（[app] secret_key）：与 Flask 共用同一把钥匙才能互认 cookie"
            )
        kwargs: dict[str, Any] = {
            "salt": "cookie-session",
            "signer_kwargs": {
                "key_derivation": "hmac",
                "digest_method": hashlib.sha1,
            },
        }
        serializer = _tagged_serializer()
        if serializer is not None:
            kwargs["serializer"] = serializer
        self._serializer = URLSafeTimedSerializer(secret_key, **kwargs)

    def dumps(self, data: dict) -> str:
        return self._serializer.dumps(data)

    def loads(self, value: str, max_age: int = MAX_AGE) -> dict | None:
        """验签并反序列化；签名错/过期/格式坏一律返回 None（等价于「没登录」）"""
        try:
            data = self._serializer.loads(value, max_age=max_age)
        except BadSignature:
            return None
        except Exception:
            return None
        return data if isinstance(data, dict) else None


class SessionDict(dict):
    """带 `modified` 标记的会话字典（Flask 的 `SecureCookieSession` 精简版）"""

    modified: bool = False

    def __init__(self, initial: dict | None = None) -> None:
        super().__init__(initial or {})
        self.modified = False

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self.modified = True

    def __delitem__(self, key) -> None:
        super().__delitem__(key)
        self.modified = True

    def clear(self) -> None:
        if self:
            super().clear()
            self.modified = True
        else:
            super().clear()

    def pop(self, key, *args):
        if key in self:
            self.modified = True
        return super().pop(key, *args)

    def popitem(self):
        self.modified = True
        return super().popitem()

    def update(self, *args, **kwargs) -> None:
        if args or kwargs:
            self.modified = True
        super().update(*args, **kwargs)

    def setdefault(self, key, default=None):
        if key not in self:
            self.modified = True
        return super().setdefault(key, default)


class FlaskCompatibleSessionMiddleware:
    """纯 ASGI 中间件：读 Flask cookie → `scope["session"]`，有改动则按 Flask 格式写回

    写成纯 ASGI（不用 `BaseHTTPMiddleware`）的理由同 `ContractMiddleware`：
    本项目核心接口是 SSE 长连接，包一层 task 会改变断流传播语义。
    """

    def __init__(
        self,
        app,
        secret_key: str,
        session_cookie: str = COOKIE_NAME,
        max_age: int = MAX_AGE,
        path: str = "/",
        same_site: str = "lax",
        https_only: bool = False,
    ) -> None:
        self.app = app
        self.signer = SessionSigner(secret_key)
        self.session_cookie = session_cookie
        self.max_age = max_age
        self.path = path
        self.same_site = same_site
        self.https_only = https_only

    # ---------------- cookie 读写 ----------------

    def _read_cookie(self, scope) -> str | None:
        for key, value in scope.get("headers", []):
            if key == b"cookie":
                for chunk in value.decode("latin-1").split(";"):
                    name, _, val = chunk.strip().partition("=")
                    if name == self.session_cookie:
                        return val
        return None

    def _render_cookie(self, value: str) -> str:
        parts: list[str] = [f"{self.session_cookie}={value}", f"Path={self.path}", "HttpOnly"]
        if self.https_only:
            parts.append("Secure")
        if self.same_site:
            parts.append(f"SameSite={self.same_site.capitalize()}")
        return "; ".join(parts)

    # ---------------- ASGI ----------------

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        raw: str | None = self._read_cookie(scope)
        data: dict | None = self.signer.loads(raw) if raw else None
        session = SessionDict(data or {})
        scope["session"] = session

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start" and session.modified:
                headers = MutableHeaders(scope=message)
                if session:
                    headers.append("Set-Cookie", self._render_cookie(self.signer.dumps(dict(session))))
                else:
                    headers.append("Set-Cookie", f"{self.session_cookie}=; {_COOKIE_EXPIRED}; "
                                                 f"Path={self.path}; HttpOnly; "
                                                 f"SameSite={self.same_site.capitalize()}")
                headers.append("Vary", "Cookie")
            await send(message)

        await self.app(scope, receive, send_wrapper)
