"""数据库连接管理（**异步**）：SQLAlchemy AsyncSession + asyncmy

2026-11 异步化后：
- 驱动从 `pymysql` 换成 `asyncmy`（cp312 有 manylinux 轮子），`AsyncSession` 全链路 await。
- 请求级 session 仍由 `DbSessionMiddleware` 创建/关闭（ContextVar 传递），但关闭要 `await`。
- `get_db()` 仍然是**同步取、异步用**（拿 session 不产生 IO，所以不必 await）：
    `db = get_db()` → `await db.execute(...)`
- 不在请求上下文里（后台任务/脚本/维护）用 `async with session_scope() as db:`。

为什么不再用同步驱动：SQLAlchemy 同步调用会阻塞事件循环；异步化后流式链路是
`async def`，任何同步阻塞都会把整条链卡住（见 `docs/10-设计-Web框架迁移评估.md` §14.5）。
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import AsyncIterator

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.declarative import declarative_base

from config.Configs import cfg
from src.infra.utils.LogUtil import log

# 用 URL.create 而不是拼字符串：密码里有 @ / : / % 时拼接会解析错
DATABASE_URL: URL = URL.create(
    "mysql+asyncmy",
    username=cfg.mysqlUser,
    password=cfg.mysqlPass,
    host=cfg.mysqlHost,
    port=int(cfg.mysqlPort),
    database=cfg.mysqlDB,
    query={"charset": "utf8mb4"},
)

engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
    # 固定会话时区为 UTC：列里有的是代码写 timezone.utc、有的是 MySQL 的
    # func.now()（服务器本地时间，CST=UTC+8）。不统一的话同一列混两种时钟——
    # 归档一次 updated_at 就"跳 8 小时"，会话列表排序和 trace 的 hours 过滤都会错。
    connect_args={"init_command": "SET time_zone = '+00:00'"},
)

SessionLocal: async_sessionmaker = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)

Base = declarative_base()

_request_session: ContextVar[AsyncSession | None] = ContextVar("pa_db_session", default=None)


def open_request_session() -> tuple[AsyncSession, object]:
    """请求进入时调用：创建 session 并绑定到当前 context，返回 (session, token)"""
    sess: AsyncSession = SessionLocal()
    token = _request_session.set(sess)
    return sess, token


async def close_request_session(sess: AsyncSession, token) -> None:
    """请求结束时调用：先解绑再关闭（顺序错了其它请求可能拿到已关闭的 session）"""
    try:
        _request_session.reset(token)
    except Exception:
        pass
    try:
        await sess.close()
    except Exception as e:  # 关连接失败不该影响响应
        log.warning(f"关闭请求级 DB session 失败: {e}")


def get_db() -> AsyncSession:
    """获取当前请求的 DB session（同一请求内多次调用返回同一个）

    没有请求上下文时（后台线程）自建一个并挂到该 context 上。
    这种用法请优先改成 `async with session_scope() as db:`，否则连接要等 GC 才回池。
    """
    sess: AsyncSession | None = _request_session.get()
    if sess is None:
        sess = SessionLocal()
        _request_session.set(sess)
        log.warning("get_db() 在无请求上下文的线程里被调用，已新建 session；建议改用 session_scope()")
    return sess


@asynccontextmanager
async def acquire_db() -> AsyncIterator[AsyncSession]:
    """**推荐用法**：有请求 session 就借用，没有就自建并在退出时关闭

    为什么需要它：`get_db()` 在没有请求上下文时会新建 session 挂在 ContextVar 上，
    **不会主动关闭**（要等 GC），SQLAlchemy 会警告
    `The garbage collector is trying to clean up non-checked-in connection`，
    连接迟迟不回池。凡是"可能被请求内调用、也可能被脚本/后台任务调用"的代码
    （如 `rbac.ais_disabled`），都应该用这个。
    """
    current: AsyncSession | None = _request_session.get()
    if current is not None:
        yield current          # 请求内：交给中间件统一关闭
        return
    async with session_scope() as sess:
        yield sess


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """非请求上下文里的用法：`async with session_scope() as db: ...`（退出即关闭）"""
    sess: AsyncSession = SessionLocal()
    token = _request_session.set(sess)
    try:
        yield sess
    finally:
        await close_request_session(sess, token)

# ---------------------------------------------------------------- #
# open-agent P3 阶段说明：
#   本文件是 FastAPI 侧使用的异步数据层；
#   `database.py`（flask.g + 同步 Session）仍由现有 Flask 入口使用。
#   P2 删掉 Flask 后，把本文件改名为 database.py 并删除旧的那份。
# ---------------------------------------------------------------- #
