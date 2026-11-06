"""登录态与鉴权（open-agent 版：**没有 CAS**，用户名+可选口令 / 免密测试登录）

与 Flask 侧的对应关系：
  `AuthUtil.get_current_user()`  → `get_current_user(request)`
  `AuthUtil.login_required`      → `Depends(require_login)`

会话 cookie 的格式与 Flask 完全一致（同一个 secret_key、同样的签名/TaggedJSON），
所以切流前后用户**不需要重新登录**，回滚同理。
"""
from fastapi import Request

from config.Configs import cfg
from src.infra.utils.LogUtil import log

PREFIX: str = "/open-agent-harness"

# 与 AuthUtil 保持一致（改这里必须同步改 AuthUtil，否则切流瞬间登录态读不出来）
SESSION_USER: str = "USERNAME"
SESSION_DISPLAY: str = "DISPLAY_NAME"


class LoginRequired(Exception):
    """未登录：API 返回 401 JSON（带 login_url），页面跳登录页"""

    def __init__(self, is_api: bool, login_url: str) -> None:
        self.is_api = is_api
        self.login_url = login_url
        super().__init__("login required")


class AccountDisabled(Exception):
    """账号已停用/删除"""

    def __init__(self, is_api: bool) -> None:
        self.is_api = is_api
        super().__init__("account disabled")


def is_api_path(path: str) -> bool:
    """接口路径（返回 JSON 而不是跳转）"""
    return path.startswith(f"{PREFIX}/api/")


def build_login_url(request: Request) -> str:
    """登录地址（带 next 回跳，与 Flask 侧口径一致）"""
    nxt: str = str(request.url.path)
    if request.url.query:
        nxt = f"{nxt}?{request.url.query}"
    return f"{PREFIX}/login?next={nxt}"


def get_current_user(request: Request) -> str:
    """从会话里取用户名（未登录返回空串）"""
    try:
        sess = request.session
    except Exception:
        return ""
    return str(sess.get(SESSION_USER) or "").strip()


def get_current_display_name(request: Request) -> str:
    try:
        return str(request.session.get(SESSION_DISPLAY) or "").strip()
    except Exception:
        return ""


async def _ais_disabled(username: str) -> bool:
    """账号停用检查（异步版，`require_login` 用）；失败放行——DB 抖动不该把所有人挡在门外。"""
    try:
        from src.infra.RbacService import rbac

        return bool(await rbac.ais_disabled(username))
    except Exception as e:
        log.warning(f"查询账号停用状态失败（放行）: user={username}, err={e}")
        return False


def _is_disabled(username: str) -> bool:
    """账号是否停用；失败放行——数据库抖动不该把所有人挡在门外"""
    try:
        from src.infra.RbacService import rbac

        return bool(rbac.is_disabled(username))
    except Exception as e:
        log.warning(f"查询账号停用状态失败（放行）: user={username}, err={e}")
        return False


async def require_login(request: Request) -> str:
    """FastAPI 依赖：校验登录并返回用户名"""
    username: str = get_current_user(request)
    if not username:
        raise LoginRequired(is_api=is_api_path(request.url.path),
                            login_url=build_login_url(request))
    if await _ais_disabled(username):
        log.warning(f"停用账号访问: user={username}, path={request.url.path}")
        raise AccountDisabled(is_api=is_api_path(request.url.path))
    return username
