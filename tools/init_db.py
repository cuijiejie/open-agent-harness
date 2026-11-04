"""初始化数据库：建表 + 灌内置角色 + 同步工具元数据（幂等，可重复执行）

用法：
    python tools/init_db.py

做的事：
  1. 建 ORM 表（chat_sessions / chat_messages 等）
  2. 建 RBAC 三张表并灌内置角色
  3. 建 pa_tools 并把代码里声明的工具同步进去

其余表（审计、轨迹、偏好、MCP、知识库）会在首次使用时自建（CREATE TABLE IF NOT EXISTS），
不需要在这里处理。
"""
import importlib
import os
import pkgutil
import sys

ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def main() -> int:
    from src.infra.db.database import Base, engine

    # 1) ORM 表：先把 model 包里所有模块导入，metadata 才完整
    import src.infra.model as model_pkg
    for mod in pkgutil.iter_modules(model_pkg.__path__):
        if not mod.name.startswith("_"):
            importlib.import_module(f"src.infra.model.{mod.name}")
    Base.metadata.create_all(engine)
    print("[1/3] ORM 表:", ", ".join(sorted(Base.metadata.tables)) or "(无)")

    # 2) 角色表 + 内置角色
    try:
        from src.infra.RbacService import rbac
        ok: bool = rbac.ensure_schema()
        print("[2/3] 角色表:", "OK" if ok else "失败（见日志）")
    except Exception as e:
        print("[2/3] 角色表失败:", e)

    # 3) 工具元数据
    try:
        from src.infra.ToolMetaService import tool_meta
        from src.agent.tools.registry import describe
        rows: list[dict] = [
            {"name": s["name"], "risk": s.get("risk", "low"), "note": s.get("note", "")}
            for s in describe()
        ]
        n: int = tool_meta.sync(rows)
        print(f"[3/3] 工具元数据: {len(rows)} 个工具，同步 {n} 行")
    except Exception as e:
        print("[3/3] 工具元数据失败:", e)

    print("初始化完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
