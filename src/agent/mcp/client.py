"""MCP 客户端（langchain-mcp-adapters）：stdio / streamable_http / sse 三种传输

为什么换成官方适配器
--------------------
原来是自己手写的 stdio JSON-RPC（行分隔协议）。能用，但只认"本机起进程"这一种传输，
而现实里对方给的 MCP 服务八成是 HTTP 端点（`POST /mcp` 的 streamable_http，或旧的 SSE）。
官方的 `langchain_mcp_adapters` 把这几种传输、会话初始化、通知、流式响应都实现了，
我们只负责：**从库里读配置 → 建连接 → 把工具包装成本项目的命名与权限口径**。

安全边界（重要，别松）
----------------------
- **stdio**：会在应用机上起进程，等于远程执行，所以只能写在 `configs.cfg` 里由运维声明，
  **页面不提供新增/修改**（接口层硬拒），命令白名单校验在 `server_bean.ALLOWED_COMMANDS`。
- **streamable_http / sse**：只是由应用主动出网去调别人的服务，不落地执行任何东西，
  所以允许管理员在页面上自助配置（含鉴权头），但每次都记审计日志。

配置（configs.cfg，仅 stdio 需要；远程服务走库/页面）
----------------------------------------------------
    [mcp]
    enabled=true
    servers=[{"name":"local","transport":"stdio","command":"python3","args":["/opt/mcp/server.py"]}]
    timeout=30

调用模型
--------
`get_tools()` 在后台事件循环里发现工具（只列清单，用完即关）；
真正调用时适配器为**每次调用新建一个会话**（stdio 起进程 / http 建连接），
所以工具可以被 uvicorn 主循环里的 agent 直接 await，不存在跨 loop 复用连接的问题。

对外命名保持 `mcp_<服务名>_<工具名>`：RBAC、工具管理、角色页都按这个前缀匹配，不能改。
"""
from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from src.infra.utils.LogUtil import log

MAX_SERVERS: int = 20
MAX_TOOLS_PER_SERVER: int = 30
MAX_OUTPUT_CHARS: int = 8000
# 页面可自助配置的远程传输：**只支持 streamable_http**（MCP 2025-03-26 起的标准传输）
# stdio 只能来自配置文件：它在应用机上起进程，等于远程执行
REMOTE_TRANSPORTS: tuple = ("streamable_http",)
ALLOWED_TRANSPORTS: tuple = ("stdio",) + REMOTE_TRANSPORTS
DEFAULT_TRANSPORT: str = "streamable_http"


# ---------------------------------------------------------------- 后台事件循环
class _Bridge:
    """同步代码（路由、预热、装配）调用 async 适配器的桥

    uvicorn 的同步路由跑在线程池里，没有 running loop；直接 `asyncio.run()`
    会在"已经在事件循环里"的场景炸掉。这里固定一个后台 loop，
    所有"发现工具/探测连接"都在它上面跑，调用方只管同步拿结果。
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="mcp-bridge", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Any, timeout: float = 60.0) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)


_bridge: _Bridge | None = None
_bridge_lock = threading.Lock()


def _get_bridge() -> _Bridge:
    global _bridge
    with _bridge_lock:
        if _bridge is None:
            _bridge = _Bridge()
        return _bridge


# ---------------------------------------------------------------- 配置读取
def build_connection(srv: dict, timeout: int = 30) -> dict | None:
    """把库里/配置里的一行 → 适配器认识的 connection dict

    返回 None 表示这条配置不合法（缺 url / 缺 command / 传输不认识）。
    """
    name: str = str(srv.get("name") or "").strip()
    if not name:
        return None
    transport: str = str(srv.get("transport") or "").strip().lower()
    if not transport:
        # 老数据没有 transport 列：有 url 就是远程，否则按 stdio
        transport = "streamable_http" if str(srv.get("url") or "").strip() else "stdio"
    if transport not in ALLOWED_TRANSPORTS:
        return None

    if transport == "stdio":
        command: str = str(srv.get("command") or "").strip()
        if not command:
            return None
        conn: dict = {
            "transport": "stdio",
            "command": command,
            "args": [str(a) for a in (srv.get("args") or [])],
        }
        env = srv.get("env")
        if isinstance(env, dict) and env:
            conn["env"] = {str(k): str(v) for k, v in env.items()}
        return conn

    url: str = str(srv.get("url") or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    headers = srv.get("headers")
    conn = {"transport": transport, "url": url, "timeout": float(timeout)}
    if isinstance(headers, dict) and headers:
        conn["headers"] = {str(k): str(v) for k, v in headers.items()}
    return conn


def normalize_headers(raw: Any) -> dict:
    """请求头：接受 dict 或 'K: V' 逐行的字符串，返回干净 dict"""
    if isinstance(raw, dict):
        return {str(k).strip(): str(v).strip() for k, v in raw.items() if str(k).strip()}
    out: dict = {}
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def validate_remote(name: str, transport: str, url: str) -> str:
    """页面新增/编辑时的校验；返回空串表示通过"""
    import re as _re

    if not _re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,23}", str(name or "")):
        return "服务名只能用小写字母、数字、下划线或短横线（≤24 字符）"
    if transport not in REMOTE_TRANSPORTS:
        return "页面只支持 streamable_http（stdio 请写在配置文件里）"
    if not str(url or "").lower().startswith(("http://", "https://")):
        return "URL 必须以 http:// 或 https:// 开头"
    return ""


# ---------------------------------------------------------------- 结果规整
def _to_text(result: Any) -> str:
    """适配器返回值 → 纯文本（可能是 str / content blocks / (content, artifact)）"""
    if isinstance(result, tuple) and result:
        result = result[0]
    if isinstance(result, str):
        text = result
    elif isinstance(result, list):
        parts: list[str] = []
        for item in result:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif "text" in item:
                    parts.append(str(item.get("text") or ""))
            elif item is not None:
                parts.append(str(item))
        text = "\n".join(p for p in parts if p)
    elif isinstance(result, dict):
        text = str(result.get("text") or json.dumps(result, ensure_ascii=False))
    else:
        text = "" if result is None else str(result)
    text = text.strip()
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + f"\n…（输出已截断，共 {len(text)} 字符）"
    return text or "（工具执行成功，无文本输出）"


class McpManager:
    """按库/配置管理 MCP 服务，并把它们的工具暴露给 agent"""

    def __init__(self) -> None:
        self._tools_cache: list[BaseTool] | None = None
        # 探测结果：name → {"ok": bool, "error": str, "tools": [..], "ts": float}
        self._probe: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---------- 服务清单 ----------
    def _servers_from_cfg(self) -> list[dict]:
        """服务器清单：库为准；配置文件里新增的（库里没有）补进表

        顺序：读库 → 读配置 → 配置里有库里没有的名字就 INSERT IGNORE → 以库为准返回。
        """
        db_rows: list[dict] = []
        try:
            from src.agent.mcp.server_bean import mcp_servers

            db_rows = mcp_servers.list_servers()
        except Exception as e:
            log.warning(f"读取 pa_mcp_servers 失败（回落配置文件）: {e}")

        cfg_rows: list[dict] = self._servers_from_file()
        if cfg_rows:
            try:
                from src.agent.mcp.server_bean import mcp_servers

                added = mcp_servers.seed(cfg_rows)
                if added:
                    log.info(f"配置文件里有 {added} 个新 MCP 服务，已补进 pa_mcp_servers")
                    db_rows = mcp_servers.list_servers()
            except Exception as e:
                log.warning(f"MCP 配置与表同步失败（本轮用配置）: {e}")

        rows = db_rows or cfg_rows
        return rows[:MAX_SERVERS]

    @staticmethod
    def _servers_from_file() -> list[dict]:
        """读 configs.cfg 的 [mcp] servers（JSON 数组）"""
        try:
            from config.Configs import cfg

            if not getattr(cfg, "mcpEnabled", False):
                return []
            raw: str = str(getattr(cfg, "mcpServers", "") or "").strip()
            if not raw or raw in ("[]", ""):
                return []
            data = json.loads(raw)
            if not isinstance(data, list):
                log.warning("MCP servers 配置不是 JSON 数组，忽略")
                return []
            out: list[dict] = []
            for item in data[:MAX_SERVERS]:
                if isinstance(item, dict) and item.get("name") and build_connection(item):
                    out.append(item)
            return out
        except Exception as e:
            log.warning(f"解析 MCP 配置失败（忽略）: {e}")
            return []

    @staticmethod
    def _timeout() -> int:
        try:
            from config.Configs import cfg

            return int(getattr(cfg, "mcpTimeout", 30) or 30)
        except Exception:
            return 30

    def _connections(self, servers: list[dict]) -> dict[str, dict]:
        timeout = self._timeout()
        out: dict[str, dict] = {}
        for srv in servers:
            conn = build_connection(srv, timeout=timeout)
            if not conn:
                log.warning(f"MCP 服务配置不完整，跳过: {srv.get('name')}")
                continue
            out[str(srv["name"])[:24]] = conn
        return out

    # ---------- 工具装配 ----------
    def load_tools(self) -> list[BaseTool]:
        """返回所有 MCP server 的工具（结果缓存；**一台连不上不影响其它台**）"""
        with self._lock:
            if self._tools_cache is not None:
                return self._tools_cache
            servers = self._servers_from_cfg()
            conns = self._connections(servers)
            tools: list[BaseTool] = []
            if not conns:
                self._tools_cache = []
                return []
            import time as _t

            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient

                client = MultiServerMCPClient(conns, tool_name_prefix=False)
            except Exception as e:
                log.warning(f"MCP 适配器不可用（本轮不挂载 MCP 工具）: {e}")
                self._tools_cache = []
                return []
            timeout = max(30, self._timeout() + 10)
            for srv_name, conn in conns.items():
                try:
                    raw_tools = _get_bridge().run(client.get_tools(server_name=srv_name), timeout=timeout)
                except Exception as e:
                    self._probe[srv_name] = {"ok": False, "error": str(e)[:300], "tools": [], "ts": _t.time()}
                    log.warning(f"MCP 服务连接失败（跳过，不影响其它服务）: {srv_name}"
                                f"（{conn.get('transport')}），err={str(e)[:200]}")
                    continue
                picked = raw_tools[:MAX_TOOLS_PER_SERVER]
                names: list[str] = []
                for t in picked:
                    tools.append(self._wrap(t, srv_name))
                    names.append(str(getattr(t, "name", "")))
                self._probe[srv_name] = {"ok": True, "error": "", "tools": names, "ts": _t.time()}
                log.info(f"MCP 服务已接入: {srv_name}（{conn.get('transport')}），工具={len(picked)}")
            self._tools_cache = tools
            return tools

    @staticmethod
    def _wrap(tool: Any, server: str) -> BaseTool:
        """适配器工具 → 本项目命名的工具（异步；失败返回文本而不是抛异常）"""
        raw_name: str = str(getattr(tool, "name", "") or "tool")
        short: str = raw_name
        for pre in (f"{server}_", f"{server}__"):
            if short.startswith(pre):
                short = short[len(pre):]
                break
        desc: str = str(getattr(tool, "description", "") or "")[:500] or f"MCP {server} 的工具 {short}"

        async def _run(**kwargs: Any) -> str:
            try:
                log.info(f"MCP 工具调用: server={server}, tool={short}")
                return _to_text(await tool.ainvoke(kwargs))
            except Exception as e:
                log.warning(f"MCP 工具调用失败: server={server}, tool={short}, err={e}")
                return f"调用 MCP 工具失败（{server}/{short}）: {str(e)[:300]}"

        return StructuredTool(
            name=f"mcp_{server}_{short}"[:64],
            description=f"[MCP {server}] {desc}",
            args_schema=getattr(tool, "args_schema", None),
            coroutine=_run,
        )

    # ---------- 管理面 ----------
    def describe_servers(self, connect: bool = True) -> list[dict]:
        """管理面用：列出已配置的 MCP 服务（按需探测，探测结果带工具清单）"""
        if connect:
            try:
                self.load_tools()
            except Exception as e:
                log.warning(f"MCP 列表前连接失败（继续展示配置）: {e}")
        out: list[dict] = []
        for srv in self._servers_from_cfg():
            name: str = str(srv.get("name") or "")
            conn = build_connection(srv, timeout=self._timeout())
            tools: list[str] = [
                t.name for t in (self._tools_cache or []) if t.name.startswith(f"mcp_{name}_")
            ]
            probe = self._probe.get(name) or {}
            out.append({
                "name": name,
                "display_name": str(srv.get("display_name") or ""),
                "tool_names": srv.get("tool_names") or {},
                "transport": str(srv.get("transport") or (conn or {}).get("transport") or "stdio"),
                "url": str(srv.get("url") or ""),
                "command": str(srv.get("command") or ""),
                "args": [str(a) for a in (srv.get("args") or [])],
                "headers": normalize_headers(srv.get("headers")) if srv.get("headers") else {},
                "note": str(srv.get("note") or ""),
                "enabled": bool(srv.get("enabled", True)),
                "editable": str(srv.get("transport") or "") in REMOTE_TRANSPORTS,
                "connected": bool(probe.get("ok")) if probe else bool(conn and tools),
                "last_error": str(probe.get("error") or "")[:300],
                "tools": tools,
                "tool_count": len(tools),
            })
        return out

    def test_server(self, name: str) -> dict:
        """连一次某个服务并返回工具清单（stdio 起进程、http 建连接）"""
        import time as _t

        srv = next((s for s in self._servers_from_cfg() if str(s.get("name")) == name), None)
        if not srv:
            return {"ok": False, "error": f"未配置的服务: {name}"}
        conn = build_connection(srv, timeout=self._timeout())
        if not conn:
            return {"ok": False, "error": "服务配置不完整（缺 command 或 url）"}
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            client = MultiServerMCPClient({name: conn}, tool_name_prefix=False)
            tools = _get_bridge().run(client.get_tools(server_name=name), timeout=max(30, self._timeout() + 10))
            names = [str(getattr(t, "name", "")) for t in tools][:MAX_TOOLS_PER_SERVER]
            self._probe[name] = {"ok": True, "error": "", "tools": names, "ts": _t.time()}
            self._tools_cache = None      # 下次装配重新生成
            try:
                from src.infra.utils import CacheUtil

                CacheUtil.delete("cache:mcp:servers")
            except Exception:
                pass
            return {"ok": True, "name": name, "transport": conn.get("transport"),
                    "tools": [{"name": n, "description": ""} for n in names]}
        except Exception as e:
            self._probe[name] = {"ok": False, "error": str(e)[:300], "tools": [], "ts": _t.time()}
            return {"ok": False, "error": str(e)[:300]}

    def reload(self) -> dict:
        """清空工具缓存与探测结果（配置改动后 / 服务升级后调用）"""
        self.close_all()
        return {"ok": True, "servers": len(self._servers_from_cfg())}

    def close_all(self) -> None:
        self._tools_cache = None
        self._probe.clear()


mcp_manager = McpManager()

__all__ = [
    "mcp_manager", "McpManager", "build_connection", "normalize_headers",
    "validate_remote", "REMOTE_TRANSPORTS", "ALLOWED_TRANSPORTS",
]
