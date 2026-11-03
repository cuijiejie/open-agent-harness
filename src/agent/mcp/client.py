"""MCP 客户端（stdio）：把外部 MCP server 的工具接成 LangChain 工具

为什么值得有
------------
harness 的"工具生态"有两种来源：① 内置/技能（本项目的 tools/ + tools.json）
② **外部服务**（公司里的工单、指标、知识库）。MCP 就是 ② 的标准协议：
server 一配，工具自动出现在模型清单里，不用改代码、不用重新发版。

安全边界（重要）
----------------
- MCP server 跑在**宿主机**（不是用户沙箱），所以只能由**管理员在配置里声明**，
  **绝不支持用户上传/自装** —— 否则等于给用户一个在服务器上执行任意程序的口子。
- server 命令走白名单：只允许配置里明确写出的 command（不读用户输入）。
- 调用超时（默认 30s）+ 输出截断，server 崩溃后下次调用自动重启。

配置（configs.cfg）
------------------
    [mcp]
    enabled=false
    ; JSON 数组：[{\"name\":\"demo\",\"command\":\"python3\",\"args\":[\"/path/server.py\"]}]
    servers=[]
    timeout=30
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field, create_model

from src.infra.utils.LogUtil import log

_PROTOCOL_VERSION = "2024-11-05"
_PY_TYPES: dict[str, Any] = {
    "string": str, "integer": int, "number": float, "boolean": bool,
    "array": list, "object": dict,
}
MAX_SERVERS: int = 5
MAX_TOOLS_PER_SERVER: int = 30
MAX_OUTPUT_CHARS: int = 8000


class McpStdioClient:
    """一个 MCP server 的 stdio 连接（行分隔 JSON-RPC）"""

    def __init__(self, name: str, command: str, args: list[str], env: dict | None = None,
                 timeout: int = 30) -> None:
        self.name = name
        self.command = command
        self.args = args or []
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._seq = 0
        self._env = {**os.environ, **(env or {})}

    # ---------- 进程与读写 ----------
    def _start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        self._proc = subprocess.Popen(
            [self.command, *self.args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1, env=self._env,
        )
        threading.Thread(target=self._reader, args=(self._proc, self._q), daemon=True).start()
        threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True).start()
        self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "open-agent-harness", "version": "1.0"},
        })
        self._notify("notifications/initialized")
        log.info(f"MCP server 已连接: {self.name} ({self.command} {' '.join(self.args)})")

    @staticmethod
    def _reader(proc: subprocess.Popen, q: queue.Queue) -> None:
        for line in proc.stdout or []:
            q.put(line)

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        for line in proc.stderr or []:
            if line.strip():
                log.warning(f"MCP[{self.name}] stderr: {line.rstrip()[:300]}")

    def _send(self, payload: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("MCP server 未启动")
        self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str, params: dict | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _request(self, method: str, params: dict | None = None) -> dict:
        """发请求并等对应 id 的响应"""
        with self._lock:
            self._seq += 1
            req_id: int = self._seq
            msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
            self._send(msg)
            deadline: float = time.time() + self.timeout
            while time.time() < deadline:
                try:
                    raw = self._q.get(timeout=max(0.1, deadline - time.time()))
                except queue.Empty:
                    break
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if data.get("id") != req_id:
                    continue          # 通知或别的响应，忽略
                if "error" in data:
                    raise RuntimeError(f"MCP 错误: {data['error']}")
                return data.get("result") or {}
            raise TimeoutError(f"MCP[{self.name}] {method} 超时（{self.timeout}s）")

    # ---------- MCP 能力 ----------
    def list_tools(self) -> list[dict]:
        self._start()
        res = self._request("tools/list", {})
        return list(res.get("tools") or [])[:MAX_TOOLS_PER_SERVER]

    def call(self, tool_name: str, arguments: dict) -> str:
        self._start()
        res = self._request("tools/call", {"name": tool_name, "arguments": arguments or {}})
        parts: list[str] = []
        for item in res.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        text: str = "\n".join(parts).strip()
        if res.get("isError"):
            return f"调用失败: {text[:500]}"
        if len(text) > MAX_OUTPUT_CHARS:
            text = text[:MAX_OUTPUT_CHARS] + f"\n…（输出已截断，共 {len(text)} 字符）"
        return text or "（工具执行成功，无文本输出）"

    def close(self) -> None:
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
        except Exception:
            pass


def _schema_from_json(json_schema: dict) -> Any:
    """MCP 的 inputSchema(JSON Schema) → pydantic 参数模型"""
    props: dict = (json_schema or {}).get("properties") or {}
    required: set = set((json_schema or {}).get("required") or [])
    fields: dict[str, Any] = {}
    for name, spec in list(props.items())[:20]:
        spec = spec if isinstance(spec, dict) else {}
        typ = _PY_TYPES.get(str(spec.get("type") or "string").lower(), str)
        desc: str = str(spec.get("description") or "")[:200]
        if name in required:
            fields[name] = (typ, Field(..., description=desc))
        else:
            fields[name] = (typ, Field(spec.get("default", "" if typ is str else None), description=desc))
    return create_model("mcp_args", **fields) if fields else None


class McpManager:
    """按配置管理 MCP server，并把它们的工具暴露给 agent"""

    def __init__(self) -> None:
        self._clients: dict[str, McpStdioClient] = {}
        self._tools_cache: list[BaseTool] | None = None
        self._lock = threading.Lock()

    def _servers_from_cfg(self) -> list[dict]:
        """服务器清单：库为准；**每次都检查配置文件里有没有库里没有的新服务并补进表**

        顺序：读库 → 读配置 → 配置里有库里没有的名字就 INSERT IGNORE → 以库为准返回。
        这样"在配置里加一台服务"仍然生效，而库里已有记录（包括将来页面上改过的）不会被覆盖。
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

        return db_rows or cfg_rows

    @staticmethod
    def _servers_from_file() -> list[dict]:
        """只在 pa_mcp_servers 为空时使用：读 configs.cfg 的 [mcp] servers"""
        try:
            from config.Configs import cfg

            if not getattr(cfg, "mcpEnabled", False):
                return []
            raw: str = str(getattr(cfg, "mcpServers", "") or "").strip()
            if not raw:
                return []
            data = json.loads(raw)
            if not isinstance(data, list):
                log.warning("MCP servers 配置不是 JSON 数组，忽略")
                return []
            out: list[dict] = []
            for item in data[:MAX_SERVERS]:
                if isinstance(item, dict) and item.get("command") and item.get("name"):
                    out.append(item)
            return out
        except Exception as e:
            log.warning(f"解析 MCP 配置失败（忽略）: {e}")
            return []

    def load_tools(self) -> list[BaseTool]:
        """返回所有 MCP server 的工具（结果缓存；失败不影响主流程）"""
        with self._lock:
            if self._tools_cache is not None:
                return self._tools_cache
            tools: list[BaseTool] = []
            timeout: int = 30
            try:
                from config.Configs import cfg

                timeout = int(getattr(cfg, "mcpTimeout", 30) or 30)
            except Exception:
                pass

            for srv in self._servers_from_cfg():
                name: str = str(srv["name"])[:24]
                client = McpStdioClient(
                    name=name,
                    command=str(srv["command"]),
                    args=[str(a) for a in (srv.get("args") or [])],
                    env=srv.get("env") if isinstance(srv.get("env"), dict) else None,
                    timeout=timeout,
                )
                self._clients[name] = client
                try:
                    specs = client.list_tools()
                except Exception as e:
                    log.warning(f"MCP server 工具列表失败: {name}, err={e}")
                    continue
                for spec in specs:
                    tname: str = str(spec.get("name") or "")[:40]
                    if not tname:
                        continue
                    tools.append(self._build_tool(client, name, tname, spec))
                log.info(f"MCP server 已接入: {name}, 工具={len(specs)}")

            self._tools_cache = tools
            return tools

    @staticmethod
    def _build_tool(client: McpStdioClient, server: str, tname: str, spec: dict) -> BaseTool:
        desc: str = str(spec.get("description") or "")[:500] or f"MCP {server} 的工具 {tname}"
        schema = _schema_from_json(spec.get("inputSchema") or {})

        def _run(**kwargs: Any) -> str:
            try:
                log.info(f"MCP 工具调用: server={server}, tool={tname}")
                return client.call(tname, kwargs)
            except Exception as e:
                return f"调用 MCP 工具失败（{server}/{tname}）: {e}"

        return StructuredTool.from_function(
            func=_run,
            name=f"mcp_{server}_{tname}"[:64],
            description=f"[MCP {server}] {desc}",
            args_schema=schema,
        )

    def describe_servers(self, connect: bool = True) -> list[dict]:
        """管理面用：列出已配置的 MCP 服务（**按需连接**，连上后带工具清单）

        为什么要在这里 load_tools()：gunicorn 多 worker 各自持有连接，
        管理接口的请求可能落到还没连过的 worker 上 —— 不主动连就会出现
        "刚测试连接成功、列表却显示未连接"的假象。
        """
        if connect:
            # connect=True：主动连一遍（首次/多 worker 切换时保证状态准确），约 1.3s
            try:
                self.load_tools()      # 幂等：已连过直接返回缓存
            except Exception as e:
                log.warning(f"MCP 列表前连接失败（继续展示配置）: {e}")
        out: list[dict] = []
        for srv in self._servers_from_cfg():
            name: str = str(srv.get("name") or "")
            client = self._clients.get(name)
            alive: bool = bool(client and client._proc and client._proc.poll() is None)
            tools: list[str] = [
                t.name for t in (self._tools_cache or []) if t.name.startswith(f"mcp_{name}_")
            ]
            out.append({
                "name": name,
                "display_name": str(srv.get("display_name") or ""),
                "tool_names": srv.get("tool_names") or {},
                "command": str(srv.get("command") or ""),
                "args": [str(a) for a in (srv.get("args") or [])],
                "connected": alive,
                "tools": tools,
                "tool_count": len(tools),
            })
        return out

    def test_server(self, name: str) -> dict:
        """重连某个服务并刷新工具清单（返回它当前能提供的工具）"""
        srv = next((s for s in self._servers_from_cfg() if str(s.get("name")) == name), None)
        if not srv:
            return {"ok": False, "error": f"未配置的服务: {name}"}
        try:
            timeout: int = 30
            from config.Configs import cfg

            timeout = int(getattr(cfg, "mcpTimeout", 30) or 30)
        except Exception:
            timeout = 30
        old = self._clients.pop(name, None)
        if old:
            old.close()
        client = McpStdioClient(
            name=name,
            command=str(srv["command"]),
            args=[str(a) for a in (srv.get("args") or [])],
            env=srv.get("env") if isinstance(srv.get("env"), dict) else None,
            timeout=timeout,
        )
        try:
            specs = client.list_tools()
        except Exception as e:
            self._clients[name] = client
            return {"ok": False, "error": str(e)[:300]}
        self._clients[name] = client
        self._tools_cache = None       # 让下次装配重新生成
        return {
            "ok": True,
            "name": name,
            "tools": [{"name": str(s.get("name") or ""),
                       "description": str(s.get("description") or "")[:120]} for s in specs],
        }

    def reload(self) -> dict:
        """断掉所有连接并清空工具缓存（配置改动后 / 服务升级后调用）"""
        self.close_all()
        return {"ok": True, "servers": len(self._servers_from_cfg())}

    def close_all(self) -> None:
        for c in self._clients.values():
            c.close()
        self._clients.clear()
        self._tools_cache = None


mcp_manager = McpManager()

__all__ = ["mcp_manager", "McpManager", "McpStdioClient"]
