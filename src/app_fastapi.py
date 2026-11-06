#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FastAPI 应用入口（P0 骨架）

启动：`uvicorn src.app_fastapi:app --workers 6`

P0 只做**框架层**：会话（与 Flask 双向兼容）、鉴权依赖、中间件、登录/登出、静态文件、
版本探针。业务接口仍由 Flask 的 `src/app.py` 提供 —— 两条入口可以并存，
用端口区分（Flask:5002 / FastAPI:5003），确认行为一致后再切。

P1 才把 60 个 controller 路由搬过来，P2 再删 Flask。
"""
import asyncio
import os
import re

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from jinja2 import Template

from config.Configs import cfg
from src.web.routers import (audit, chat, knowledge, mcp, platform_skill,
                             prefs, rbac, sandbox, skill, trace)
from src.infra.utils.LogUtil import log
from src.web.middleware import RequestContextMiddleware
from src.web.pages import LOGIN_HTML
from src.web.security import (PREFIX, SESSION_DISPLAY, SESSION_USER, AccountDisabled,
                              LoginRequired, build_login_url, get_current_display_name,
                              get_current_user, is_api_path, require_login)
from src.web.session import FlaskCompatibleSessionMiddleware

_DIST_DIR: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist"
)

async def lifespan(_app: FastAPI):
    """启动时初始化异步 checkpointer

    LangGraph 的 `astream` 要求异步 saver（`AsyncPostgresSaver`），而 agent 管理器是
    模块级单例（import 期就存在），只能在这里把实例准备好；`setup()` 幂等，
    老数据（同步 saver 写的）可以直接读，不需要迁库。
    """
    from src.infra.CheckpointerManager import init_checkpointer_async

    await init_checkpointer_async()
    log.info(f"FastAPI 启动: prefix={PREFIX}, pid={os.getpid()}")
    # 安全自检：没有密码的账号等于"知道用户名也进不来"，但 admin 必须是可用的
    try:
        from src.infra.RbacService import rbac as _rbac

        _adm: dict | None = _rbac.user_row("admin")
        if _adm is not None and not _adm["password_hash"]:
            log.warning("安全警告：admin 账号未设置密码，当前无人可登录；"
                        "请执行 python -m deploy.seed_admin 初始化（默认密码 admin）")
    except Exception:
        pass
    yield
    log.info(f"FastAPI 关闭: pid={os.getpid()}")


app: FastAPI = FastAPI(
    title="Open Agent Harness",
    lifespan=lifespan,
    docs_url=None, redoc_url=None, openapi_url=None,     # 不对外暴露文档
)

# 与 Flask-CORS(app) 默认行为一致
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

# 会话：cookie 名/签名/序列化与 Flask 完全一致 → 切流、回滚都不需要重新登录
app.add_middleware(FlaskCompatibleSessionMiddleware, secret_key=cfg.secretKey)
app.add_middleware(RequestContextMiddleware)

# 业务路由：router 里写 /api/xxx，统一挂到 PREFIX 下
for _r in (audit, trace, chat, knowledge, prefs, mcp, rbac, sandbox, skill, platform_skill):
    app.include_router(_r.router, prefix=PREFIX)


@app.exception_handler(Exception)
async def _on_unhandled(request: Request, exc: Exception):
    """兜底 500：接口回 JSON，页面回纯文本"""
    log.error(f"未捕获异常: {request.method} {request.url.path}: {type(exc).__name__}: {exc}",
              exc_info=True)
    if is_api_path(request.url.path):
        return JSONResponse({"error": "服务器繁忙，请稍后重试"}, status_code=500)
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse("服务器繁忙，请稍后重试", status_code=500)

if os.path.isdir(os.path.join(_DIST_DIR, "assets")):
    app.mount(f"{PREFIX}/assets", StaticFiles(directory=os.path.join(_DIST_DIR, "assets")),
              name="assets")


# ---------------- 未登录 / 停用 的统一响应 ----------------

@app.exception_handler(LoginRequired)
async def _on_login_required(request: Request, exc: LoginRequired):
    if exc.is_api:
        return JSONResponse({"error": "未登录", "login_url": exc.login_url},
                            status_code=401, headers={"Cache-Control": "no-store"})
    return RedirectResponse(exc.login_url, status_code=302)


@app.exception_handler(AccountDisabled)
async def _on_account_disabled(request: Request, exc: AccountDisabled):
    if exc.is_api:
        return JSONResponse({"error": "账号已停用，请联系管理员"}, status_code=403)
    return RedirectResponse(f"{PREFIX}/login", status_code=302)


# ---------------- 静态页与探针 ----------------

def _serve(name: str):
    from fastapi.responses import FileResponse

    p: str = os.path.join(_DIST_DIR, name)
    if not os.path.isfile(p):
        return JSONResponse({"error": f"前端未构建: {name}"}, status_code=404)
    return FileResponse(p)


@app.get("/")
async def root(request: Request):
    """根路径：未登录去登录页，已登录给前端首页"""
    if not get_current_user(request):
        return RedirectResponse(build_login_url(request), status_code=302)
    return _serve("index.html")


@app.get(f"{PREFIX}/", include_in_schema=False)
async def index(request: Request):
    if not get_current_user(request):
        return RedirectResponse(build_login_url(request), status_code=302)
    return _serve("index.html")


@app.get(f"{PREFIX}/api/version")
async def api_version():
    """前端构建信息（排缓存用），口径与 Flask 完全一致"""
    try:
        idx: str = os.path.join(_DIST_DIR, "index.html")
        html: str = open(idx, encoding="utf-8").read() if os.path.isfile(idx) else ""
        js: str = (re.findall(r"assets/main-[A-Za-z0-9_-]+\.js", html) or [""])[0]
        mtime: float = os.path.getmtime(idx) if os.path.isfile(idx) else 0
        return JSONResponse({"ok": True, "frontend_js": js, "index_mtime": mtime})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)})


@app.get(f"{PREFIX}/api/user/currentUser")
async def current_user(username: str = Depends(require_login), request: Request = None):
    return JSONResponse({
        "userAccount": username,
        "username": username,
        "displayName": get_current_display_name(request),
    })


# ---------------- 登录 / 登出 ----------------

def _safe_next(nxt: str) -> str:
    """只允许站内回跳，挡住 `next=https://evil` / `//evil` / `/\\evil` 这类开放重定向"""
    raw: str = str(nxt or "").strip()
    if not raw.startswith(PREFIX + "/"):
        return f"{PREFIX}/"
    if raw.startswith("//") or "\\" in raw or any(ord(ch) < 32 for ch in raw):
        return f"{PREFIX}/"
    return raw


def _valid_username(username: str) -> bool:
    """账号格式：1~64 字符，不含路径分隔符/控制字符（防 DB 脏数据与路径穿越）"""
    u: str = str(username or "")
    if not u or len(u) > 64:
        return False
    if "/" in u or "\\" in u or ".." in u:
        return False
    if any(ch in u for ch in '<>"'):
        return False
    return not any(ord(ch) < 32 for ch in u)


def _login_rate_limited(ip: str) -> bool:
    """登录限速：同一 IP 每分钟最多 20 次（Redis 不可用/异常时放行）"""
    try:
        from src.infra.utils.RedisUtil import redis_util

        if not redis_util.available:
            return False
        key: str = f"login:rate:{ip or '-'}"
        c = redis_util._client
        count = c.incr(key)
        if count == 1:
            c.expire(key, 60)
        return int(count) > 20
    except Exception:
        return False


def _render_login(error: str = "") -> str:
    """渲染登录页

    `pages.LOGIN_HTML` 是 **Jinja 模板**（从 Flask 的 `AuthUtil` 抽出来时保持原样），
    必须用 Jinja 渲染再返回；直接 `HTMLResponse(LOGIN_HTML)` 会把 `{{ hint }}`、
    `{% if need_password %}` 原样吐到页面上（切 FastAPI 后真实发生过的 bug）。
    变量口径与 Flask 侧完全一致：hint / need_password / error。
    """
    hint: str = "请使用管理员分配的账号和密码登录。"
    return Template(LOGIN_HTML).render(hint=hint, error=error)


@app.get(f"{PREFIX}/login")
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(f"{PREFIX}/", status_code=302)
    from fastapi.responses import HTMLResponse

    return HTMLResponse(_render_login())


@app.post(f"{PREFIX}/login")
async def login_submit(request: Request):
    from fastapi.responses import HTMLResponse

    form = await request.form()
    username: str = str(form.get("username") or "").strip()
    password: str = str(form.get("password") or "")
    nxt: str = _safe_next(str(request.query_params.get("next") or f"{PREFIX}/"))

    from src.web.http import client_ip

    if _login_rate_limited(client_ip(request)):
        log.warning(f"登录被限速: user={username}, ip={client_ip(request)}")
        return HTMLResponse(_render_login("登录过于频繁，请稍后再试"), status_code=429)
    if not username:
        return HTMLResponse(_render_login("用户名不能为空"), status_code=400)
    if not _valid_username(username):
        return HTMLResponse(_render_login("用户名不合法（不能含 / \\ .. 或控制字符）"), status_code=400)
    if not password:
        return HTMLResponse(_render_login("请输入密码"), status_code=400)
    from src.infra.RbacService import rbac as _rbac

    _row: dict | None = _rbac.user_row(username)
    # 账号不存在 / 已停用 → 同一提示，不泄露账号是否存在
    if not _row or str(_row.get("status") or "") != "active":
        log.warning(f"登录失败（账号不存在或已停用）: user={username}, ip={client_ip(request)}")
        return HTMLResponse(_render_login("用户名或密码错误"), status_code=401)
    if not _row.get("password_hash"):
        log.warning(f"登录失败（账号未设密码，需管理员重置）: user={username}")
        return HTMLResponse(_render_login("该账号尚未设置密码，请联系管理员"), status_code=401)
    if not _rbac.verify_password(username, password):
        log.warning(f"登录失败（密码错误）: user={username}, ip={client_ip(request)}")
        return HTMLResponse(_render_login("用户名或密码错误"), status_code=401)

    from src.infra.RbacService import rbac

    rbac.touch_user(username, _row.get("display_name") or "")
    request.session[SESSION_USER] = username
    request.session[SESSION_DISPLAY] = ""
    log.info(f"登录成功: user={username}")
    _init_sandbox_async(username)
    return RedirectResponse(nxt, status_code=302)


@app.api_route(f"{PREFIX}/logout", methods=["GET", "POST"])
async def logout(request: Request):
    user: str = get_current_user(request)
    request.session.clear()
    log.info(f"退出登录: user={user}")
    return RedirectResponse(f"{PREFIX}/login", status_code=302)


def _init_sandbox_async(username: str) -> None:
    """登录后预热沙箱（失败不影响登录）

    直接调 sandbox 的后台初始化（与 `/api/warm` 同一入口），不再依赖已删除的 AuthUtil。
    """
    try:
        from src.agent.backends.sandbox import init_user_sandbox_async

        init_user_sandbox_async(username)
    except Exception as e:
        log.warning(f"沙箱预热失败（忽略）: user={username}, err={e}")
