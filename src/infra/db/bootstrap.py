"""启动时的数据库自检：确保 ORM 表存在（幂等）

为什么放在启动流程里：
    pa_* 这些表都带 `CREATE TABLE IF NOT EXISTS`，第一次用到就会自建；
    但 `chat_sessions` / `chat_messages` 是 SQLAlchemy 声明式模型，
    **没有任何地方会自建**。少了它们，第一次对话写历史就会报
    "table doesn't exist"，而错误会被 `_safe_error()` 收成一句通用提示，
    排查成本极高。所以启动时统一 ensure 一遍。

多 worker 同时执行是安全的：DDL 全部幂等，MySQL 内部串行化。
失败只记录日志、不阻断启动 —— 让「数据库没准备好」以明确的 ERROR 暴露，
而不是整个服务起不来（反过来，服务起来了但连不上库，请求会自然报错）。
"""
import importlib
import pkgutil

from src.infra.db.database import Base, engine
from src.infra.utils.LogUtil import log


def ensure_tables() -> None:
    """建 ORM 表（已存在则跳过）"""
    try:
        # 先把 model 包里所有模块导入，metadata 才是完整的
        import src.infra.model as model_pkg

        for mod in pkgutil.iter_modules(model_pkg.__path__):
            if not mod.name.startswith("_"):
                importlib.import_module(f"src.infra.model.{mod.name}")

        tables = sorted(Base.metadata.tables)
        Base.metadata.create_all(engine)
        log.info(f"数据库自检完成：{len(tables)} 张表 {tables}")
    except Exception as e:  # noqa: BLE001
        log.error(
            "数据库自检失败（建表未完成，首次对话可能报 table doesn't exist）：%s", e
        )
