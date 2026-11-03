"""checkpointer 管理模块：PostgresSaver 连接池创建与全局单例

职责：
- LangGraph 持久化基础设施（PostgreSQL 连接池 + 建表迁移 + setup 冲突重试）
- 与 Agent 业务解耦，便于独立维护/测试/替换后端
  （如需换回 SQLite/MemorySaver，只需修改本文件）

关键点：
- 使用 psycopg_pool.ConnectionPool（连接池），而非 PostgresSaver.from_conn_string()
  （后者是 contextmanager，退出即关闭连接，不适合全局长生命周期单例）。
- 连接池天然适配多 gunicorn 进程：自动管理连接、复用、并发安全。
- kwargs={"autocommit": True}：与官方 from_conn_string 行为一致，
  建表迁移中的 CREATE INDEX CONCURRENTLY 不能运行在事务块内。
- setup() 加冲突重试：gunicorn 多 worker 并发启动时会同时执行 setup()，
  首次建表瞬间可能互相冲突（UniqueViolation），捕获后短等待重试；
  表已由其他 worker 建好后 setup() 幂等，重试即可通过。
- PG 连接信息来自配置 agent.pg_*。
"""
import threading
import time as _time

from langgraph.checkpoint.postgres import PostgresSaver
from src.infra.utils.LogUtil import log
from config.Configs import cfg


class CheckpointerManager:
    """PostgresSaver 全局单例管理（线程安全懒加载）"""

    _instance: PostgresSaver | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get_checkpointer(cls):
        """获取全局唯一的 checkpointer（线程安全，懒加载）

        没配 PostgreSQL（pg_host 为空）时退化成内存实现：功能照常，
        但进程重启后对话上下文会丢——这是"最小可运行"和"生产可用"的分界。
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    if not str(getattr(cfg, "pgHost", "") or "").strip():
                        from langgraph.checkpoint.memory import InMemorySaver

                        log.warning(
                            "未配置 PostgreSQL（[agent] pg_host 为空），"
                            "使用内存 checkpointer：重启后对话上下文会丢失"
                        )
                        cls._instance = InMemorySaver()
                    else:
                        cls._instance = cls._create_checkpointer()
        return cls._instance

    @classmethod
    def _create_checkpointer(cls) -> PostgresSaver:
        """创建 PostgresSaver（PostgreSQL 连接池）并完成建表迁移"""
        from psycopg_pool import ConnectionPool
        from psycopg.errors import UniqueViolation as _PgUniqueViolation

        conninfo: str = (
            f"host={cfg.pgHost} port={cfg.pgPort} dbname={cfg.pgDb} "
            f"user={cfg.pgUser} password={cfg.pgPass}"
        )
        # autocommit=True：建表迁移中的 CREATE INDEX CONCURRENTLY 不能运行在事务块内
        pool = ConnectionPool(
            conninfo,
            min_size=1, max_size=10, open=False,
            kwargs={"autocommit": True},
        )
        pool.open(wait=True, timeout=10)
        saver = PostgresSaver(pool)
        # 提前 setup，确保建表 + 迁移生效，并暴露早期连接错误；
        # 多 worker 并发首次建表冲突时重试（幂等，表建好后秒过）。
        # 关键：**setup 失败必须抛异常**。以前 3 次冲突后照样返回并永久缓存这个
        # "半初始化"的 saver，日志还写"初始化完成"，之后所有 checkpoint 读写都失败、
        # 多轮上下文全丢，却查不出原因。
        last_err: Exception | None = None
        for _attempt in range(3):
            try:
                saver.setup()
                last_err = None
                break
            except _PgUniqueViolation as e:
                last_err = e
                log.warning(f"PostgresSaver setup 冲突(第{_attempt + 1}次): {e}，等待后重试")
                _time.sleep(2)
            except Exception as e:
                last_err = e
                break
        if last_err is not None:
            # 失败路径必须关掉连接池，否则每次请求都会新建一个池（PG 连接泄漏）
            try:
                pool.close()
            except Exception:
                pass
            raise RuntimeError(f"PostgresSaver 初始化失败（建表/迁移未完成）: {last_err}")
        log.info(f"PostgresSaver 初始化完成: {cfg.pgHost}:{cfg.pgPort}/{cfg.pgDb}")
        return saver


# 向后兼容的模块级访问（懒加载由 get_checkpointer 保证）
def get_checkpointer() -> PostgresSaver:
    """获取全局 PostgresSaver 实例"""
    return CheckpointerManager.get_checkpointer()


# 类型别名，便于调用方标注类型而不直接依赖 langgraph 包
CheckpointerType = PostgresSaver
