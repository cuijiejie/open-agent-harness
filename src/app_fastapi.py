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


@app.get(f"{PREFIX}/test.html")
async def test_page():
    return _serve("test.html")


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

def _need_password() -> bool:
    return bool(str(getattr(cfg, "loginPassword", "") or "").strip())


@app.get(f"{PREFIX}/login")
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(f"{PREFIX}/", status_code=302)
    from fastapi.responses import HTMLResponse

    return HTMLResponse(LOGIN_HTML)


@app.post(f"{PREFIX}/login")
async def login_submit(request: Request):
    from fastapi.responses import HTMLResponse

    form = await request.form()
    username: str = str(form.get("username") or "").strip()
    password: str = str(form.get("password") or "")
    nxt: str = str(request.query_params.get("next") or f"{PREFIX}/")

    if not username:
        return HTMLResponse("用户名不能为空", status_code=400)
    if _need_password() and password != cfg.loginPassword:
        log.warning(f"登录失败（口令错误）: user={username}")
        return HTMLResponse("用户名或口令错误", status_code=401)

    from src.infra.RbacService import rbac

    if rbac.is_disabled(username):
        return HTMLResponse("账号已停用，请联系管理员", status_code=403)
    rbac.touch_user(username, "")
    request.session[SESSION_USER] = username
    request.session[SESSION_DISPLAY] = ""
    log.info(f"登录成功: user={username}")
    _init_sandbox_async(username)
    return RedirectResponse(nxt, status_code=302)


@app.get(f"{PREFIX}/test-login")
async def test_login_page():
    return RedirectResponse(f"{PREFIX}/login", status_code=302)


@app.post(f"{PREFIX}/test-login")
async def test_login(request: Request):
    """免密直登（脚本/冒烟测试用），需 `[app] test_login_enabled=true`"""
    if not cfg.testLoginEnabled:
        log.warning("test-login 被拒绝（配置未启用）")
        return JSONResponse({"error": "测试登录功能未启用"}, status_code=403)

    ct: str = request.headers.get("content-type") or ""
    username: str = ""
    if "application/json" in ct:
        try:
            username = str((await request.json() or {}).get("username", "")).strip()
        except Exception:
            username = ""
    else:
        form = await request.form()
        username = str(form.get("username") or "").strip()
    if not username:
        return JSONResponse({"error": "用户名不能为空"}, status_code=400)

    from src.infra.RbacService import rbac

    if rbac.is_disabled(username):
        log.warning(f"停用账号尝试测试登录: user={username}")
        return JSONResponse({"error": "账号已停用，请联系管理员"}, status_code=403)
    rbac.touch_user(username, "")
    request.session[SESSION_USER] = username
    request.session[SESSION_DISPLAY] = ""
    log.warning(f"【测试登录】免密登录成功: user={username}")
    _init_sandbox_async(username)
    return RedirectResponse(f"{PREFIX}/", status_code=302)


@app.api_route(f"{PREFIX}/logout", methods=["GET", "POST"])
async def logout(request: Request):
    user: str = get_current_user(request)
    request.session.clear()
    log.info(f"退出登录: user={user}")
    return RedirectResponse(f"{PREFIX}/login", status_code=302)


def _init_sandbox_async(username: str) -> None:
    """登录后预热沙箱（失败不影响登录）

    P0 复用 Flask 侧的实现；P1 把它搬到 `src/web/`，避免这里 import AuthUtil 时带出 flask。
    """
    try:
        from src.infra.utils.AuthUtil import _init_sandbox

        _init_sandbox(username)
    except Exception as e:
        log.warning(f"沙箱预热失败（忽略）: user={username}, err={e}")
