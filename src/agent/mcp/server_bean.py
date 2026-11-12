"""MCP 服务清单（pa_mcp_servers）：**唯一来源是这张表，页面写、客户端读**

为什么只有远程
--------------
MCP 只支持 streamable_http：一条记录 = 名字 + 地址（+ 鉴权头）。
不存 command / args / env —— 那套是"在应用机上起进程"的 stdio，等于远程执行，
不能开放给界面，本项目也不做（部署与运维成本都更高，见 docs/MCP接入说明.md）。

安全
----
    · 只有管理员能写（接口层校验 + 审计日志）
    · `enabled=0` 的服务不参与工具装配
    · 地址只允许 http(s)，由管理员填写（信任边界=管理员）
"""
from __future__ import annotations

import json

from src.infra.utils.LogUtil import log

_DDL = """
CREATE TABLE IF NOT EXISTS pa_mcp_servers (
  name       VARCHAR(32)  NOT NULL PRIMARY KEY,
  transport  VARCHAR(16)  NOT NULL DEFAULT 'streamable_http',
  url        VARCHAR(512) NOT NULL DEFAULT '',
  headers    JSON,
  display_name VARCHAR(64)  NOT NULL DEFAULT '',
  tool_names JSON,
  note       VARCHAR(255) NOT NULL DEFAULT '',
  enabled    TINYINT      NOT NULL DEFAULT 1,
  sort_no    INT          NOT NULL DEFAULT 100,
  updated_by VARCHAR(64)  NOT NULL DEFAULT '',
  updated_at TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""

# 老库迁移（幂等）：补远程字段；把 stdio 时代的 command/args/env 删掉
_ALTERS = (
    "ALTER TABLE pa_mcp_servers ADD COLUMN display_name VARCHAR(64) NOT NULL DEFAULT ''",
    "ALTER TABLE pa_mcp_servers ADD COLUMN tool_names JSON NULL",
    "ALTER TABLE pa_mcp_servers ADD COLUMN transport VARCHAR(16) NOT NULL DEFAULT 'streamable_http'",
    "ALTER TABLE pa_mcp_servers ADD COLUMN url VARCHAR(512) NOT NULL DEFAULT ''",
    "ALTER TABLE pa_mcp_servers ADD COLUMN headers JSON NULL",
    # stdio 已下线：这三列不再使用（表里本来也只有远程服务）
    "ALTER TABLE pa_mcp_servers DROP COLUMN command",
    "ALTER TABLE pa_mcp_servers DROP COLUMN args",
    "ALTER TABLE pa_mcp_servers DROP COLUMN env",
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
        """[{name, transport, url, headers, display_name, tool_names, note, enabled}]"""
        if not self.ensure_schema():
            return []
        try:
            with self._conn() as c, c.cursor() as cur:
                sql = ("SELECT name, transport, url, headers, display_name, tool_names, note, enabled "
                       "FROM pa_mcp_servers "
                       + ("" if include_disabled else "WHERE enabled=1 ")
                       + "ORDER BY sort_no, name")
                cur.execute(sql)
                rows = cur.fetchall()
        except Exception as e:
            log.warning(f"读取 pa_mcp_servers 失败: {e}")
            return []

        out: list[dict] = []
        for r in rows:
            try:
                headers = json.loads(r[3]) if r[3] else {}
            except Exception:
                headers = {}
            try:
                tnames = json.loads(r[5]) if r[5] else {}
            except Exception:
                tnames = {}
            out.append({
                "name": str(r[0]),
                "transport": str(r[1] or "streamable_http"),
                "url": str(r[2] or ""),
                "headers": headers if isinstance(headers, dict) else {},
                "display_name": str(r[4] or ""),
                "tool_names": tnames if isinstance(tnames, dict) else {},
                "note": str(r[6] or ""),
                "enabled": bool(r[7]),
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

    # ---------- 页面自助管理（只有远程：名字 + 地址 + 鉴权头） ----------
    def create_server(self, name: str, transport: str, url: str, headers: dict | None = None,
                      display_name: str = "", note: str = "", actor: str = "") -> dict:
        if not self.ensure_schema():
            return {"ok": False, "error": "数据库不可用"}
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT 1 FROM pa_mcp_servers WHERE name=%s", (name,))
                if cur.fetchone():
                    return {"ok": False, "error": f"服务名 {name} 已存在"}
                cur.execute(
                    "INSERT INTO pa_mcp_servers (name, transport, url, headers, display_name, note, enabled, updated_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s,1,%s)",
                    (name, transport, url,
                     json.dumps(headers or {}, ensure_ascii=False),
                     display_name[:64], note[:255], actor[:64]),
                )
            log.warning(f"MCP 服务已新增: name={name}, url={url}, actor={actor}")
            return {"ok": True, "name": name}
        except Exception as e:
            log.error(f"新增 MCP 服务失败: {name}, err={e}")
            return {"ok": False, "error": "新增失败（详见服务端日志）"}

    def update_server(self, name: str, transport: str | None = None, url: str | None = None,
                      headers: dict | None = None, display_name: str | None = None,
                      note: str | None = None, enabled: bool | None = None,
                      actor: str = "") -> dict:
        if not self.ensure_schema():
            return {"ok": False, "error": "数据库不可用"}
        sets: list[str] = []
        args: list = []
        if transport is not None:
            sets.append("transport=%s"); args.append(transport)
        if url is not None:
            sets.append("url=%s"); args.append(url[:512])
        if headers is not None:
            sets.append("headers=%s"); args.append(json.dumps(headers, ensure_ascii=False))
        if display_name is not None:
            sets.append("display_name=%s"); args.append(str(display_name)[:64])
        if note is not None:
            sets.append("note=%s"); args.append(str(note)[:255])
        if enabled is not None:
            sets.append("enabled=%s"); args.append(1 if enabled else 0)
        if not sets:
            return {"ok": False, "error": "没有要修改的字段"}
        sets.append("updated_by=%s"); args.append(actor[:64])
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT 1 FROM pa_mcp_servers WHERE name=%s", (name,))
                if not cur.fetchone():
                    return {"ok": False, "error": f"MCP 服务 {name} 不在表里"}
                cur.execute(f"UPDATE pa_mcp_servers SET {', '.join(sets)} WHERE name=%s", (*args, name))
            log.warning(f"MCP 服务已修改: name={name}, fields={[x.split('=')[0] for x in sets]}, actor={actor}")
            return {"ok": True, "name": name}
        except Exception as e:
            log.error(f"修改 MCP 服务失败: {name}, err={e}")
            return {"ok": False, "error": "修改失败（详见服务端日志）"}

    def delete_server(self, name: str, actor: str = "") -> dict:
        if not self.ensure_schema():
            return {"ok": False, "error": "数据库不可用"}
        try:
            with self._conn() as c, c.cursor() as cur:
                cur.execute("SELECT 1 FROM pa_mcp_servers WHERE name=%s", (name,))
                if not cur.fetchone():
                    return {"ok": False, "error": f"MCP 服务 {name} 不在表里"}
                cur.execute("DELETE FROM pa_mcp_servers WHERE name=%s", (name,))
            log.warning(f"MCP 服务已删除: name={name}, actor={actor}")
            return {"ok": True, "name": name}
        except Exception as e:
            log.error(f"删除 MCP 服务失败: {name}, err={e}")
            return {"ok": False, "error": "删除失败（详见服务端日志）"}


mcp_servers = McpServerService()
__all__ = ["mcp_servers", "McpServerService"]
