"""MySQL 连接池（SQLAlchemy 引擎的 QueuePool + pymysql）

为什么用它
----------
`pa_trace_log` / `pa_audit_log` 是**每个工具步骤**写一条的热点表：一轮对话 2~10 次
写入。之前每次 `pymysql.connect()` 都要 TCP 握手 + 认证（本机实测 ~1.5~3.7ms），
高并发时还会把 MySQL 的 max_connections 打满。

为什么是 SQLAlchemy 的池
------------------------
- 项目已经依赖 SQLAlchemy（ORM 那套在用），**不引入新包**（生产内网装不了）；
- 池由 `create_engine()` 创建，带上 dialect 才能用 `pool_pre_ping`（借出前探活，
  断线自动重建）、`pool_recycle`（定期重建，避开 MySQL wait_timeout）、
  `pool_reset_on_return='rollback'`（归还时回滚残留事务）。
  注意：**不能**直接 `QueuePool(pre_ping=True)` —— 裸池没有 dialect，会报
  "The ping feature requires that a dialect is passed to the connection pool"。

用法（仍然是原生 DBAPI 连接，现有 pymysql 写法不用改）
----------------------------------------------------
    conn = get_conn()          # 从池里借（_ConnectionFairy，代理 DBAPI 连接）
    try:
        ...
        conn.commit()
    except Exception:
        conn.rollback()
        conn.invalidate()      # 连接已坏 → 标记作废，别还回池里
        raise
    finally:
        conn.close()           # **归还到池**（不是真的关连接）
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from config.Configs import cfg
from src.infra.utils.LogUtil import log

# 用 URL.create 而不是拼字符串：口令里含 @ / : / # 等字符也不会解析错
_URL = URL.create(
    "mysql+pymysql",
    username=cfg.mysqlUser,
    password=cfg.mysqlPass,
    host=cfg.mysqlHost,
    port=int(cfg.mysqlPort),
    database=cfg.mysqlDB,
)

# 池要小：trace/审计是"写一条就还回去"的短借短还，单进程同一时刻最多几个连接。
# 6 worker × (2 + 4 overflow) = 最多 36 条，对 MySQL 毫无压力。
# pool_timeout=5：借不到连接最多等 5s（宁可丢一条 trace，也不阻塞对话）。
_engine = create_engine(
    _URL,
    pool_size=2,
    max_overflow=4,
    pool_timeout=5,
    pool_recycle=1800,
    pool_pre_ping=True,
    pool_reset_on_return="rollback",
    # 我们只用原生连接（raw_connection），不需要 ORM 的 identity map 等特性
    future=True,
)


def get_conn():
    """从池里借一条原生连接（用完必须 close() 归还）"""
    return _engine.raw_connection()


def status() -> dict:
    """池状态（排障/自检用）"""
    try:
        p = _engine.pool
        return {
            "size": p.size(),
            "checkedout": p.checkedout(),
            "checkedin": p.checkedin(),
            "overflow": p.overflow(),
        }
    except Exception as e:
        log.warning(f"读取连接池状态失败: {e}")
        return {}


def dispose() -> None:
    """关闭池里所有连接（进程退出/测试收尾用）"""
    try:
        _engine.dispose()
    except Exception as e:
        log.warning(f"释放连接池失败: {e}")


__all__ = ["get_conn", "status", "dispose"]
