"""启动时的数据库自检：确保 ORM 表存在 + 统一排序规则（都幂等）

为什么放在启动流程里：
    1. `pa_*` 这些表都带 `CREATE TABLE IF NOT EXISTS`，第一次用到就会自建；
       但 `chat_sessions` / `chat_messages` 是 SQLAlchemy 声明式模型，
       **没有任何地方会自建**。少了它们，第一次对话写历史就会报
       "table doesn't exist"，而错误会被 `_safe_error()` 收成一句通用提示，
       排查成本极高。
    2. 排序规则必须一致：`pa_*` 建表时显式写了 utf8mb4_unicode_ci，
       而 ORM 表跟随数据库默认（常见是 utf8mb4_general_ci）。
       两者一旦不一致，`s.username = u.username` 这类跨表 JOIN 会直接报
       `Illegal mix of collations`，症状是「用户列表 0 人」「某页一直空白」——
       查询异常被上层 try 吞掉，最难查。这里统一成 utf8mb4_unicode_ci。

异步化后：引擎是 AsyncEngine，`create_all` 要用 `run_sync` 跑在异步连接里。
FastAPI 的 lifespan 里 `await ensure_tables()`（见 src/app_fastapi.py）。

多 worker 同时执行是安全的：DDL 全部幂等，MySQL 内部串行化。
失败只记录日志、不阻断启动。
"""

import importlib
import pkgutil

from sqlalchemy import text

from src.infra.db.database import Base, engine
from src.infra.utils.LogUtil import log

# 统一口径：全库内部以 unicode_ci 比较（中文、大小写不敏感）
COLLATION = "utf8mb4_unicode_ci"

# 应用自建的表（ORM + pa_*）。ALPHABET 之外的表不动，避免碰别人家的库
_APP_TABLES = (
    "chat_sessions",
    "chat_messages",
    "pa_users",
    "pa_roles",
    "pa_user_roles",
    "pa_tools",
    "pa_mcp_servers",
    "pa_user_prefs",
    "pa_knowledge",
    "pa_audit_log",
    "pa_trace_log",
)


async def _normalize_collation() -> None:
    """把应用自己的表的排序规则统一（只改不一致的，已有数据不动内容）"""
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE()"
                )
            )
        ).fetchall()
        existing = {name: coll for name, coll in rows}

        fixed: list[str] = []
        for table in _APP_TABLES:
            coll = existing.get(table)
            if not coll or coll == COLLATION:
                continue
            try:
                # DDL 在 MySQL 里隐式提交，逐条独立执行
                await conn.execute(
                    text(
                        f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 "
                        f"COLLATE {COLLATION}"
                    )
                )
                await conn.commit()
                fixed.append(f"{table}({coll})")
            except Exception as e:  # noqa: BLE001
                log.warning("排序规则统一失败: %s -> %s: %s", table, COLLATION, e)
        if fixed:
            log.warning("已统一排序规则到 %s：%s", COLLATION, "、".join(fixed))


async def ensure_tables() -> None:
    """建表（已存在则跳过）并统一排序规则"""
    try:
        # 先把 model 包里所有模块导入，metadata 才是完整的
        import src.infra.model as model_pkg

        for mod in pkgutil.iter_modules(model_pkg.__path__):
            if not mod.name.startswith("_"):
                importlib.import_module(f"src.infra.model.{mod.name}")

        tables = sorted(Base.metadata.tables)
        async with engine.begin() as conn:
            # 多 worker 同时启动会并发 create_all：checkfirst 的"先查再建"在进程之间
            # 不是原子的，会撞 1050 Table already exists。用 MySQL 命名锁串行化。
            await conn.execute(text("SELECT GET_LOCK('pa_schema_init', 60)"))
            try:
                await conn.run_sync(Base.metadata.create_all)
            finally:
                await conn.execute(text("SELECT RELEASE_LOCK('pa_schema_init')"))
        log.info(f"数据库自检完成：{len(tables)} 张表 {tables}")

        await _normalize_collation()
    except Exception as e:  # noqa: BLE001
        log.error(
            "数据库自检失败（建表未完成，首次对话可能报 table doesn't exist）：%s", e
        )
