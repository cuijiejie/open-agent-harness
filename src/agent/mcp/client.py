"""MCP 客户端（langchain-mcp-adapters）：只走 streamable_http

设计取舍
--------
**只支持一种传输：streamable_http**（MCP 2025-03-26 规范里的远程传输）。
- 不做 stdio：那要在应用机上起进程，等于把"远程执行"开给界面，安全边界守不住，
  部署时还要管依赖、管进程、管僵尸；远程端点只要一个 URL。
- 不做 SSE：老传输，新服务基本不用，多一套只会让排障多一个分支。

所以"接一个 MCP 服务" = 管理员在页面上填 **名称 + 地址（+ 鉴权头）**，
参数全部在地址的 query 串和请求头里，没有 command / args / env 这些东西。
服务清单的唯一来源是 `pa_mcp_servers` 表（页面写、这里读）。

安全边界
--------
远程地址由**管理员**填写，应用会主动出网去请求它（含内网地址）——这等价于
"管理员可以指定应用访问哪"，所以只有管理员能增删改，且每次写操作都记审计日志。

调用模型
--------
`get_tools()` 在后台事件循环里发现工具（只列清单，用完即关）；
真正调用时适配器为**每次调用新建一次会话**（HTTP 连接），
所以工具可以被 uvicorn 主循环里的 agent 直接 await。

对外命名 `mcp_<服务名>_<工具名>`：RBAC、工具管理、角色页都按这个前缀匹配，不能改。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

from src.infra.utils.LogUtil import log

MAX_SERVERS: int = 20
MAX_TOOLS_PER_SERVER: int = 30
MAX_OUTPUT_CHARS: int = 8000
# 唯一的传输；留成元组是为了界面/接口都从同一处取，避免"两套写法"
TRANSPORTS: tuple = ("streamable_http",)
DEFAULT_TRANSPORT: str = "streamable_http"
# 工具缓存存活时间（秒）：多 worker 下让"页面改完 → 对话生效"有界，见 load_tools 注释
MCP_CACHE_TTL: float = 30.0


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


# ---------------------------------------------------------------- 配置校验
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


def build_connection(srv: dict, timeout: int = 30) -> dict | None:
    """一行服务配置 → 适配器认识的 connection dict；不合法返回 None

    只有 url 一种必填（鉴权走 headers，其它参数放 url 的 query 串）。
    """
    name: str = str(srv.get("name") or "").strip()
    transport: str = str(srv.get("transport") or DEFAULT_TRANSPORT).strip().lower()
    url: str = str(srv.get("url") or "").strip()
    if not name or transport not in TRANSPORTS:
        return None
    if not url.lower().startswith(("http://", "https://")):
        return None
    conn: dict = {"transport": transport, "url": url, "timeout": float(timeout)}
    headers = normalize_headers(srv.get("headers"))
    if headers:
        conn["headers"] = headers
    return conn


def validate_remote(name: str, transport: str, url: str) -> str:
    """页面新增/编辑时的校验；返回空串表示通过"""
    import re as _re

    if not _re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,23}", str(name or "")):
        return "服务名只能用小写字母、数字、下划线或短横线（≤24 字符）"
    if transport not in TRANSPORTS:
        return "只支持 streamable_http 传输"
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
    """按库里的服务清单，把别人的 MCP 工具暴露给 agent"""

    def __init__(self) -> None:
        self._tools_cache: list[BaseTool] | None = None
        self._cache_ts: float = 0.0
        # 探测结果：name → {"ok": bool, "error": str, "tools": [..], "ts": float}
        self._probe: dict[str, dict] = {}
        self._lock = threading.Lock()

    # ---------- 服务清单：唯一来源 pa_mcp_servers ----------
    @staticmethod
    def _servers() -> list[dict]:
        try:
            from src.agent.mcp.server_bean import mcp_servers

            return mcp_servers.list_servers()[:MAX_SERVERS]
        except Exception as e:
            log.warning(f"读取 pa_mcp_servers 失败（本轮不挂载 MCP 工具）: {e}")
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
                log.warning(f"MCP 服务配置不完整（缺 url），跳过: {srv.get('name')}")
                continue
            out[str(srv["name"])[:24]] = conn
        return out

    # ---------- 工具装配 ----------
    def load_tools(self) -> list[BaseTool]:
        """返回所有 MCP 服务的工具（缓存 30 秒；**一台连不上不影响其它台**）

        缓存为什么要 TTL：uvicorn 多 worker 各持一份内存缓存，管理页新增/删除服务时
        只能清掉"处理该请求的那个 worker"的缓存。没有 TTL 的话，其它 worker 会一直
        拿着旧清单（表现为"页面显示已接入，但对话里模型说没这个工具"）。30 秒足够短，
        也避免每轮对话都去连一遍远程服务。
        """
        with self._lock:
            ts: float = time.time()
            if self._tools_cache is not None and (ts - self._cache_ts) < MCP_CACHE_TTL:
                return self._tools_cache
            conns = self._connections(self._servers())
            tools: list[BaseTool] = []
            if not conns:
                self._tools_cache = []
                self._cache_ts = ts
                return []
            try:
                from langchain_mcp_adapters.client import MultiServerMCPClient

                client = MultiServerMCPClient(conns, tool_name_prefix=False)
            except Exception as e:
                log.warning(f"MCP 适配器不可用（本轮不挂载 MCP 工具）: {e}")
                self._tools_cache = []
                self._cache_ts = ts
                return []
            timeout = max(30, self._timeout() + 10)
            for srv_name in conns:
                try:
                    raw_tools = _get_bridge().run(client.get_tools(server_name=srv_name), timeout=timeout)
                except Exception as e:
                    self._probe[srv_name] = {"ok": False, "error": str(e)[:300], "tools": [], "ts": ts}
                    log.warning(f"MCP 服务连接失败（跳过，不影响其它服务）: {srv_name}，err={str(e)[:200]}")
                    continue
                picked = raw_tools[:MAX_TOOLS_PER_SERVER]
                names: list[str] = []
                for t in picked:
                    tools.append(self._wrap(t, srv_name))
                    names.append(str(getattr(t, "name", "")))
                self._probe[srv_name] = {"ok": True, "error": "", "tools": names, "ts": ts}
                log.info(f"MCP 服务已接入: {srv_name}，工具={len(picked)}")
            self._tools_cache = tools
            self._cache_ts = ts
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
        """管理面用：列出已配置的服务（按需探测，探测结果带工具清单）"""
        if connect:
            try:
                self.load_tools()
            except Exception as e:
                log.warning(f"MCP 列表前连接失败（继续展示配置）: {e}")
        out: list[dict] = []
        for srv in self._servers():
            name: str = str(srv.get("name") or "")
            tools: list[str] = [
                t.name for t in (self._tools_cache or []) if t.name.startswith(f"mcp_{name}_")
            ]
            probe = self._probe.get(name) or {}
            # 刚点过「测试连接」但还没全量装配过工具时，_tools_cache 是空的；
            # 这里用探测结果补上，否则「设为公共」会一直报"还没有工具清单"。
            if not tools and probe.get("tools"):
                _raw: list[str] = [str(n) for n in (probe.get("tools") or []) if str(n).strip()]
                tools = [n if n.startswith(f"mcp_{name}_") else f"mcp_{name}_{n}" for n in _raw]
            out.append({
                "name": name,
                "display_name": str(srv.get("display_name") or ""),
                "tool_names": srv.get("tool_names") or {},
                "transport": str(srv.get("transport") or DEFAULT_TRANSPORT),
                "url": str(srv.get("url") or ""),
                "headers": normalize_headers(srv.get("headers")),
                "note": str(srv.get("note") or ""),
                "enabled": bool(srv.get("enabled", True)),
                "connected": bool(probe.get("ok")) if probe else bool(tools),
                "last_error": str(probe.get("error") or "")[:300],
                "tools": tools,
                "tool_count": len(tools),
            })
        return out

    def test_server(self, name: str) -> dict:
        """连一次某个服务并返回它的工具清单（排障用）"""
        srv = next((s for s in self._servers() if str(s.get("name")) == name), None)
        if not srv:
            return {"ok": False, "error": f"未配置的服务: {name}"}
        conn = build_connection(srv, timeout=self._timeout())
        if not conn:
            return {"ok": False, "error": "服务配置不完整（缺 url）"}
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            client = MultiServerMCPClient({name: conn}, tool_name_prefix=False)
            tools = _get_bridge().run(client.get_tools(server_name=name),
                                      timeout=max(30, self._timeout() + 10))
            names = [str(getattr(t, "name", "")) for t in tools][:MAX_TOOLS_PER_SERVER]
            self._probe[name] = {"ok": True, "error": "", "tools": names, "ts": time.time()}
            self._tools_cache = None      # 下次装配重新生成
            self._cache_ts = 0.0
            # 同时把清单写进库（pa_mcp_servers.tool_names）：4 个 worker 各有一份内存，
            # 只存内存的话「测试连接」之后在别的 worker 上「设为公共」会报"还没有工具清单"。
            try:
                from src.agent.mcp import server_bean as _sb

                _srv = next((x for x in _sb.mcp_servers.list_servers() if str(x.get("name")) == name), {})
                _old: dict = _srv.get("tool_names") or {}
                _sb.mcp_servers.update_meta(name, tool_names={**{n: n for n in names}, **_old})
            except Exception as e:
                log.warning(f"MCP 工具清单落库失败: server={name}, err={e}")
            try:
                from src.infra.utils import CacheUtil

                CacheUtil.delete("cache:mcp:servers")
            except Exception:
                pass
            return {"ok": True, "name": name, "transport": conn.get("transport"),
                    "tools": [{"name": n} for n in names]}
        except Exception as e:
            self._probe[name] = {"ok": False, "error": str(e)[:300], "tools": [], "ts": time.time()}
            return {"ok": False, "error": str(e)[:300]}

    def reload(self) -> dict:
        """清空工具缓存与探测结果（配置改动后调用）"""
        self.close_all()
        return {"ok": True, "servers": len(self._servers())}

    def close_all(self) -> None:
        self._tools_cache = None
        self._cache_ts = 0.0
        self._probe.clear()


mcp_manager = McpManager()

__all__ = [
    "mcp_manager", "McpManager", "build_connection", "normalize_headers",
    "validate_remote", "TRANSPORTS", "DEFAULT_TRANSPORT", "MCP_CACHE_TTL",
]
