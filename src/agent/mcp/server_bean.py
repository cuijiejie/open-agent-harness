"""MCP 服务清单（pa_mcp_servers）：与工具元数据同构，**库为准、配置只做首次种子**

为什么要这张表
--------------
`pa_tools` 已经在库里维护工具的展示/开关，而 MCP 服务器清单还只在 `configs.cfg` 的
`[mcp] servers` 里。两者不对称：一个改配置要重启，另一个点页面就生效。

现在把服务器清单也收进库：
    首次启动 → 表为空 → 把 cfg 里的 servers 灌进表（一次性种子）
    之后     → 一切以表为准，改表即改清单
              （本期页面仍是只读，只展示 + 测连接；写入能力预留但不暴露接口）

安全
----
表里存的是**可执行命令**，所以：
    · 只有管理员的手工操作能改（本期不开放页面写接口）
    · `enabled=0` 的服务不会启动
    · 命令白名单集中在 ALLOWED_COMMANDS，写入时校验（未来开放接口时直接复用）
"""
from __future__ import annotations

import json

from src.infra.utils.LogUtil import log

# 允许的启动命令（防止被塞任意可执行文件）
ALLOWED_COMMANDS: tuple = ("python3", "python", "docker", "node", "npx", "uvx", "uv")

_DDL = """
CREATE TABLE IF NOT EXISTS pa_mcp_servers (
  name       VARCHAR(32)  NOT NULL PRIMARY KEY,
  command    VARCHAR(256) NOT NULL,
  args       JSON,
  env        JSON,
  display_name VARCHAR(64)  NOT NULL DEFAULT '',
  tool_names JSON,
  note       VARCHAR(255) NOT NULL DEFAULT '',
  enabled    TINYINT      NOT NULL DEFAULT 1,
  sort_no    INT          NOT NULL DEFAULT 100,
  updated_by VARCHAR(64)  NOT NULL DEFAULT '',
  updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 老库补列（幂等）
_ALTERS = (
    "ALTER TABLE pa_mcp_servers ADD COLUMN display_name VARCHAR(64) NOT NULL DEFAULT ''",
    "ALTER TABLE pa_mcp_servers ADD COLUMN tool_names JSON NULL",
)


class McpServerService:
    def __init__(self) -> None:
        self._ready: bool = False

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
                for sql in _ALTERS:
                    try:
                        cur.execute(sql)
                    except Exception:
                        pass          # 列已存在
            self._ready = True
            return True
        except Exception as e:
            log.warning(f"pa_mcp_servers 建表失败（本轮回落配置文件）: {e}")
            return False

    # ---------- 读 ----------
    def list_servers(self, include_disabled: bool = False) -> list[dict]:
        """[{name, command, args, env, note, enabled}]；读失败返回空（调用方回落 cfg）"""
        if not self.ensure_schema():
            return []
        try:
            with self._conn() as c, c.cursor() as cur:
                sql = ("SELECT name, command, args, env, note, enabled, display_name, tool_names FROM pa_mcp_servers "
                       + ("" if include_disabled else "WHERE enabled=1 ")
                       + "ORDER BY sort_no, name")
                cur.execute(sql)
                rows = cur.fetchall()
        except Exception as e:
            log.warning(f"读取 pa_mcp_servers 失败（回落配置文件）: {e}")
            return []

        out: list[dict] = []
        for r in rows:
            try:
                args = json.loads(r[2]) if r[2] else []
            except Exception:
                args = []
            try:
                env = json.loads(r[3]) if r[3] else None
            except Exception:
                env = None
            try:
                tnames = json.loads(r[7]) if r[7] else {}
            except Exception:
                tnames = {}
            out.append({
                "name": str(r[0]), "command": str(r[1]),
                "args": [str(a) for a in (args or [])],
                "env": env if isinstance(env, dict) else None,
                "note": str(r[4] or ""), "enabled": bool(r[5]),
                "display_name": str(r[6] or ""),
                "tool_names": tnames if isinstance(tnames, dict) else {},
            })
        return out

    def update_meta(self, name: str, display_name: str | None = None,
                    tool_names: dict | None = None, note: str | None = None) -> dict:
        """只改展示用元数据（中文名/工具中文名/说明）；命令与参数不可改"""
        srv = (name or "").strip()
        if not srv or not self.ensure_schema():
            return {"ok": False, "error": "服务名无效或数据库不可用"}
        sets, args = [], []
        if display_name is not None:
            sets.append("display_name=%s"); args.append(str(display_name)[:64])
        if tool_names is not None:
            clean = {str(k): str(v)[:64] for k, v in (tool_names or {}).items() if str(k)}
            sets.append("tool_names=%s"); args.append(json.dumps(clean, ensure_ascii=False))
        if note is not None:
            sets.append("note=%s"); args.append(str(note)[:255])
        if not sets:
            return {"ok": False, "error": "没有要修改的字段"}
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT 1 FROM pa_mcp_servers WHERE name=%s", (srv,))
                if not cur.fetchone():
                    return {"ok": False, "error": f"MCP 服务 {srv} 不在表里"}
                cur.execute(f"UPDATE pa_mcp_servers SET {', '.join(sets)} WHERE name=%s", (*args, srv))
            log.warning(f"MCP 元数据更新: server={srv}, fields={[x.split('=')[0] for x in sets]}")
            return {"ok": True, "name": srv}
        except Exception as e:
            log.error(f"更新 MCP 元数据失败: {srv}, err={e}")
            return {"ok": False, "error": "更新失败（详见服务端日志）"}

    # ---------- 首次种子：从配置文件迁移 ----------
    def seed(self, cfg_servers: list[dict]) -> int:
        """表为空时把配置文件里的服务器写进来（幂等：已有同名就跳过）"""
        if not cfg_servers or not self.ensure_schema():
            return 0
        n: int = 0
        try:
            with self._conn() as c, c.cursor() as cur:
                for s in cfg_servers:
                    name: str = str(s.get("name") or "").strip()[:32]
                    command: str = str(s.get("command") or "").strip()
                    if not name or not command:
                        continue
                    cur.execute(
                        "INSERT IGNORE INTO pa_mcp_servers (name, command, args, env, note, updated_by) "
                        "VALUES (%s,%s,%s,%s,%s,%s)",
                        (name, command,
                         json.dumps([str(a) for a in (s.get("args") or [])], ensure_ascii=False),
                         json.dumps(s.get("env") or {}, ensure_ascii=False),
                         "由 configs.cfg 首次迁移", "system"),
                    )
                    n += cur.rowcount
            if n:
                log.info(f"pa_mcp_servers 首次迁移完成: {n} 个服务（来源 configs.cfg）")
        except Exception as e:
            log.warning(f"pa_mcp_servers 迁移失败: {e}")
        return n


mcp_servers = McpServerService()
__all__ = ["mcp_servers", "McpServerService", "ALLOWED_COMMANDS"]
