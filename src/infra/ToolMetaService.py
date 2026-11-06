"""工具元数据（pa_tools）：**代码提供能力，数据库提供说明/分组/风险/开关**

为什么要这一层
--------------
工具原本的元数据（名字、风险等级、说明）写死在 `tools/registry.py`，改一句说明就要发版。
现在拆成两半：

    代码（REGISTRY）→ 工具**存在不存在**（真实能力，必须发版）
    数据库（pa_tools）→ 工具**怎么展示、给不给用**（管理员在页面维护）

启动时 `sync()` 把 REGISTRY 的工具名 upsert 进表（新工具自动出现一行），
代码里删掉的工具标记 `ghost=1`（不删行，保留历史说明，页面标"代码已移除"）。

三个开关的实际含义
------------------
enabled=0     → 该工具**不装配给任何人**（连管理员也拿不到，用于紧急下线）
always_on=1   → 人人默认拥有，不参与角色分配（如 ask_user / sandbox_shell）
risk          → 只影响展示与筛选；是否允许写由角色授权决定

安全边界：这里的字段不会改变工具**行为**（函数还是代码里那个），只影响
「是否出现在清单里 / 显示什么说明 / 属于哪个分组」，所以页面维护它没有提权风险。
"""
from __future__ import annotations

from src.infra.utils.LogUtil import log

_DDL = """
CREATE TABLE IF NOT EXISTS pa_tools (
  tool_name    VARCHAR(64)  NOT NULL PRIMARY KEY,
  display_name VARCHAR(128) NOT NULL DEFAULT '',
  grp          VARCHAR(32)  NOT NULL DEFAULT '',
  risk         VARCHAR(16)  NOT NULL DEFAULT 'read',
  note         TEXT,
  always_on    TINYINT      NOT NULL DEFAULT 0,
  enabled      TINYINT      NOT NULL DEFAULT 1,
  ghost        TINYINT      NOT NULL DEFAULT 0,
  sort_no      INT          NOT NULL DEFAULT 100,
  updated_by   VARCHAR(64)  NOT NULL DEFAULT '',
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

_VALID_RISK: tuple = ("read", "write", "exec")


class ToolMetaService:
    """pa_tools 的读写。失败一律降级：读不到就用代码里的默认值（不影响主流程）"""

    def __init__(self) -> None:
        self._ready: bool = False

    # ---------- 连接 ----------
    @staticmethod
    def _conn():
        import pymysql
        from config.Configs import cfg

        return pymysql.connect(
            host=cfg.mysqlHost, port=int(cfg.mysqlPort), user=cfg.mysqlUser,
            password=cfg.mysqlPass, database=cfg.mysqlDB, charset="utf8mb4",
            autocommit=True, connect_timeout=5,
        )

    def ensure_schema(self) -> bool:
        if self._ready:
            return True
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute(_DDL)
            self._ready = True
            return True
        except Exception as e:
            log.warning(f"pa_tools 建表失败（本轮用代码默认元数据）: {e}")
            return False

    # ---------- 同步：把代码里的工具写进表 ----------
    def sync(self, specs: list[dict]) -> int:
        """specs: [{name, risk, note}]（来自 tools.registry.describe 的原始声明）

        - 新工具 → 插入一行（说明/风险取代码默认）
        - 已有工具 → 只更新 ghost=0（**不覆盖管理员改过的说明/风险/开关**）
        - 代码里没有的 → ghost=1
        """
        if not self.ensure_schema():
            return 0
        names: list[str] = [str(s.get("name")) for s in specs if s.get("name")]
        changed: int = 0
        try:
            with self._conn() as c, c.cursor() as cur:
                for s in specs:
                    name: str = str(s.get("name") or "")
                    if not name:
                        continue
                    cur.execute(
                        "INSERT INTO pa_tools (tool_name, display_name, grp, risk, note, always_on) "
                        "VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE ghost=0",
                        (name,
                         # 中文名：代码/manifest 里给了就用它，否则退回工具名；
                         # 只在**首次插入**时生效，管理员在页面改过的不覆盖
                         str(s.get("display_name") or "")[:128] or name,
                         str(s.get("group") or ""), str(s.get("risk") or "read"),
                         str(s.get("note") or ""), 1 if s.get("always_on") else 0),
                    )
                    changed += 1
                if names:
                    q = ", ".join(["%s"] * len(names))
                    # MCP 工具不在内置注册表里，不能因为"代码里没有"就被标 ghost：
                    # 它们由 MCP 服务自己声明，靠 ensure_tools() 登记
                    cur.execute(
                        f"UPDATE pa_tools SET ghost=1 "
                        f"WHERE tool_name NOT IN ({q}) AND tool_name NOT LIKE 'mcp\\_%%'",
                        tuple(names),
                    )
                else:
                    cur.execute("UPDATE pa_tools SET ghost=1 WHERE tool_name NOT LIKE 'mcp\\_%'")
            log.info(f"pa_tools 同步完成: 代码工具 {changed} 个")
        except Exception as e:
            log.warning(f"pa_tools 同步失败: {e}")
        return changed

    # ---------- 读 ----------
    def all(self) -> dict:
        """{tool_name: row dict}；读失败返回空 dict（调用方回落到代码默认）

        走 Redis（cache:tools:meta）——6 个 worker 共享，改元数据后立即全局一致。
        """
        from src.infra.utils import CacheUtil

        cached = CacheUtil.get("cache:tools:meta")
        if cached is not None:
            return cached
        if not self.ensure_schema():
            return {}
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute(
                    "SELECT tool_name, display_name, grp, risk, note, always_on, enabled, ghost, sort_no, updated_by "
                    "FROM pa_tools"
                )
                rows = cur.fetchall()
            out = {
                str(r[0]): {
                    "tool_name": str(r[0]), "display_name": str(r[1] or ""), "grp": str(r[2] or ""),
                    "risk": str(r[3] or "read"), "note": str(r[4] or ""),
                    "always_on": bool(r[5]), "enabled": bool(r[6]), "ghost": bool(r[7]),
                    "sort_no": int(r[8] or 100), "updated_by": str(r[9] or ""),
                }
                for r in rows
            }
            CacheUtil.set("cache:tools:meta", out, 120)   # 改元数据时会 delete 这个 key
            return out
        except Exception as e:
            log.warning(f"读取 pa_tools 失败（用代码默认）: {e}")
            return {}

    def flags(self) -> dict:
        """{name: (enabled, always_on)} —— 装配工具时用，读不到就全放行"""
        return {k: (v["enabled"], v["always_on"]) for k, v in self.all().items()}

    def public_names(self) -> set:
        """「公共」工具名集合（人人可用，不参与角色分配）

        单一事实来源是 pa_tools.always_on —— 工具管理页 / MCP 服务页的开关都改这一列，
        装配、角色目录、角色保存全部读它，避免代码常量与界面不一致。
        """
        try:
            return {
                str(k) for k, v in (self.all() or {}).items()
                if v.get("always_on") and v.get("enabled", True) and not v.get("ghost")
            }
        except Exception:
            return set()

    def ensure_tools(self, rows: list[dict]) -> int:
        """登记外部工具（MCP）到 pa_tools —— **只补不覆盖**

        与 sync() 的区别：sync() 会把不在清单里的工具标 ghost=1（只适用于内置工具），
        这里只做「缺了才插」，管理员改过的名字/开关/说明一律不动。
        rows: [{"name":..., "grp":..., "risk":..., "note":..., "always_on": bool}]
        """
        if not self.ensure_schema():
            return 0
        n: int = 0
        try:
            with self._conn() as c, c.cursor() as cur:
                for r in rows or []:
                    name: str = str(r.get("name") or "").strip()
                    if not name:
                        continue
                    cur.execute(
                        "INSERT INTO pa_tools (tool_name, display_name, grp, risk, note, always_on) "
                        "VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE ghost=0",
                        (
                            name, name[:128],
                            str(r.get("grp") or "MCP")[:32],
                            str(r.get("risk") or "read")[:8],
                            str(r.get("note") or "")[:2000],
                            1 if r.get("always_on") else 0,
                        ),
                    )
                    n += 1
            # 新登记的工具要让缓存失效
            from src.infra.utils import CacheUtil

            CacheUtil.delete("cache:tools:meta")
        except Exception as e:
            log.warning(f"登记外部工具失败: {e}")
        return n

    def set_public(self, names: list[str], public: bool) -> int:
        """批量设置「公共」标记（MCP 服务级开关用）"""
        cleaned: list[str] = [str(x).strip() for x in (names or []) if str(x).strip()]
        if not cleaned:
            return 0
        if not self.ensure_schema():
            return 0
        try:
            with self._conn() as c, c.cursor() as cur:
                q: str = ", ".join(["%s"] * len(cleaned))
                cur.execute(
                    f"UPDATE pa_tools SET always_on=%s, ghost=0 WHERE tool_name IN ({q})",
                    tuple([1 if public else 0] + cleaned),
                )
                n: int = cur.rowcount
            from src.infra.utils import CacheUtil

            CacheUtil.delete("cache:tools:meta")
            return n
        except Exception as e:
            log.warning(f"设置公共标记失败: {e}")
            return 0

    # ---------- 写（管理员） ----------
    def update(self, tool_name: str, fields: dict, actor: str = "") -> dict:
        name: str = (tool_name or "").strip()
        if not name:
            return {"ok": False, "error": "工具名不能为空"}
        if not self.ensure_schema():
            return {"ok": False, "error": "数据库不可用"}
        sets: list[str] = []
        args: list = []
        if "display_name" in fields:
            dn: str = str(fields.get("display_name") or "").strip()[:128]
            if not dn:
                return {"ok": False, "error": "展示名不能为空"}
            try:
                with self._conn() as c, c.cursor() as cur:
                    cur.execute(
                        "SELECT tool_name FROM pa_tools WHERE display_name=%s AND tool_name<>%s LIMIT 1",
                        (dn, name),
                    )
                    dup = cur.fetchone()
            except Exception:
                dup = None
            if dup:
                return {"ok": False, "error": f"展示名「{dn}」已被工具 {dup[0]} 使用，不能重复"}
            sets.append("display_name=%s")
            args.append(dn)
        if "grp" in fields:
            sets.append("grp=%s")
            args.append(str(fields.get("grp") or "")[:32])
        if "note" in fields:
            sets.append("note=%s")
            args.append(str(fields.get("note") or "")[:2000])
        if "risk" in fields:
            risk: str = str(fields.get("risk") or "").strip().lower()
            if risk not in _VALID_RISK:
                return {"ok": False, "error": f"风险等级只能是 {'/'.join(_VALID_RISK)}"}
            sets.append("risk=%s")
            args.append(risk)
        if "always_on" in fields:
            sets.append("always_on=%s")
            args.append(1 if fields.get("always_on") else 0)
        if "enabled" in fields:
            sets.append("enabled=%s")
            args.append(1 if fields.get("enabled") else 0)
        if "sort_no" in fields:
            try:
                sets.append("sort_no=%s")
                args.append(int(fields.get("sort_no")))
            except (TypeError, ValueError):
                pass
        if not sets:
            return {"ok": False, "error": "没有要修改的字段"}
        sets.append("updated_by=%s")
        args.append(str(actor or "")[:64])
        try:
            with self._conn() as c, c.cursor() as cur:
                # 先确认工具存在：不能拿 rowcount 判断——MySQL 在"值没变"时也返回 0
                cur.execute("SELECT 1 FROM pa_tools WHERE tool_name=%s", (name,))
                if not cur.fetchone():
                    return {"ok": False, "error": f"工具 {name} 不在元数据表里（先重启服务同步一次）"}
                cur.execute(f"UPDATE pa_tools SET {', '.join(sets)} WHERE tool_name=%s", (*args, name))
            # 写库成功 → 删缓存（不覆盖写，避免并发互相盖）
            try:
                from src.infra.utils import CacheUtil

                CacheUtil.delete("cache:tools:meta", "cache:rbac:catalog")
            except Exception:
                pass
            log.warning(f"工具元数据更新: tool={name}, actor={actor}, fields={list(fields)}")
            return {"ok": True, "tool_name": name}
        except Exception as e:
            log.error(f"更新工具元数据失败: {name}, err={e}")
            return {"ok": False, "error": "更新失败（详见服务端日志）"}


tool_meta = ToolMetaService()
__all__ = ["tool_meta", "ToolMetaService"]
