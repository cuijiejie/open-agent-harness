"""checkpointer 管理模块：AsyncPostgresSaver 连接池创建与全局单例

职责：
- LangGraph 持久化基础设施（PostgreSQL 异步连接池 + 建表迁移 + setup 冲突重试）
- 与 Agent 业务解耦，便于独立维护/测试/替换后端

关键点（2026-11 异步化后）：
- 用 `AsyncPostgresSaver` + `AsyncConnectionPool`：LangGraph 的 `astream` **要求**异步
  checkpointer，同步 `PostgresSaver` 会让 `astream` 直接报未实现。
- 表结构完全相同，同步 saver 写的历史可以直接被异步 saver 读（实测同一条 thread
  两边都读到同样的消息数），**不需要数据迁移**。
- 连接池**绑定创建它的事件循环**：只能在应用主循环里用，不能丢到别的线程
  `asyncio.run` 里（会报 "Future attached to a different loop"）。
- `init_async()` 必须在 FastAPI 的 lifespan 里 `await` 一次；`AgentManager` 是模块级
  单例（import 期就构造），所以它不能在这里取实例，而是懒取（见 main_agent）。
- kwargs={"autocommit": True}：建表迁移里的 CREATE INDEX CONCURRENTLY 不能跑在事务块内。
- setup() 多 worker 并发首次建表可能撞 UniqueViolation，重试即可（幂等）。
"""
from __future__ import annotations

import asyncio
import time as _time
from typing import Any

from src.infra.utils.LogUtil import log
from config.Configs import cfg


class CheckpointerManager:
    """AsyncPostgresSaver 全局单例管理"""

    _instance: Any = None

    @classmethod
    def instance(cls) -> Any:
        return cls._instance

    @classmethod
    def get_checkpointer(cls) -> Any:
        """获取已初始化的实例；未初始化直接报错（不要静默造一个半成品）"""
        if cls._instance is None:
            raise RuntimeError(
                "checkpointer 尚未初始化：FastAPI 启动时 lifespan 会 await init_async()，"
                "脚本/评测请先 `await CheckpointerManager.init_async()`"
            )
        return cls._instance

    @classmethod
    async def init_async(cls) -> Any:
        """创建 AsyncPostgresSaver（异步连接池）并完成建表迁移

        幂等：重复调用返回同一个实例（6 个 worker 各自初始化自己的池）。
        """
        if cls._instance is not None:
            return cls._instance

        from psycopg_pool import AsyncConnectionPool
        from psycopg.errors import UniqueViolation as _PgUniqueViolation
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        conninfo: str = (
            f"host={cfg.pgHost} port={cfg.pgPort} dbname={cfg.pgDb} "
            f"user={cfg.pgUser} password={cfg.pgPass}"
        )
        pool = AsyncConnectionPool(
            conninfo,
            min_size=1, max_size=10, open=False,
            kwargs={"autocommit": True},
        )
        await pool.open(wait=True, timeout=10)
        saver = AsyncPostgresSaver(pool)

        # 提前 setup：确保建表 + 迁移生效，并暴露早期连接错误。
        # **setup 失败必须抛异常**：以前 3 次冲突后照样返回并永久缓存这个
        # "半初始化"的 saver，日志还写"初始化完成"，之后所有 checkpoint 读写都失败、
        # 多轮上下文全丢，却查不出原因。
        last_err: Exception | None = None
        for _attempt in range(3):
            try:
                await saver.setup()
                last_err = None
                break
            except _PgUniqueViolation as e:
                last_err = e
                log.warning(f"AsyncPostgresSaver setup 冲突(第{_attempt + 1}次): {e}，等待后重试")
                await asyncio.sleep(2)
            except Exception as e:
                last_err = e
                break
        if last_err is not None:
            # 失败路径必须关掉连接池，否则每次请求都会新建一个池（PG 连接泄漏）
            try:
                await pool.close()
            except Exception:
                pass
            raise RuntimeError(f"AsyncPostgresSaver 初始化失败（建表/迁移未完成）: {last_err}")

        cls._instance = saver
        log.info(f"AsyncPostgresSaver 初始化完成: {cfg.pgHost}:{cfg.pgPort}/{cfg.pgDb}")
        return saver


# 向后兼容的模块级访问
def get_checkpointer() -> Any:
    """获取全局 AsyncPostgresSaver 实例"""
    return CheckpointerManager.get_checkpointer()


async def init_checkpointer_async() -> Any:
    """在应用启动时调用一次（lifespan）"""
    return await CheckpointerManager.init_async()


# 类型别名（调用方标注用，不直接依赖 langgraph 包）
CheckpointerType = Any
