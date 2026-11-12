#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP 链路端到端自检（streamable_http，离线可跑）

做什么
------
1. 用 `test/integration/mcp_weather_server.py`（stub 模式，不联网）拉起一台本地 MCP 服务；
2. 用项目的 `McpManager` 走完整链路：读配置 → 建连接 → `tools/list` → 包装成
   `mcp_<服务>_<工具>` → `ainvoke` 调用 → 返回值规整成文本；
3. 再放一台坏服务（端口不通），断言**好的照常挂着、坏的记录错误**。

为什么不在 test/unit
---------------------
单测约定不连网、不起进程；这里要真起一个 HTTP 服务并走 MCP 协议。

跑法
----
    PYTHONPATH=. <env>/bin/python test/integration/mcp_e2e.py
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("PA_LLM_DEEPSEEK_API_KEY", "sk-placeholder-for-integration")
os.environ["MCP_WEATHER_STUB"] = "1"

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'✓' if ok else '✗'} {name}{(' — ' + detail) if detail and not ok else ''}")
    if not ok:
        FAILED.append(name)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_health(port: int, timeout: float = 25.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                return r.status == 200
        except Exception:
            time.sleep(0.3)
    return False


def main() -> int:
    port = free_port()
    env = {**os.environ, "PYTHONPATH": ROOT, "MCP_WEATHER_STUB": "1"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "test.integration.mcp_weather_server:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        print(f"[1/3] 启动示例 MCP 服务 :{port}（stub）")
        if not wait_health(port):
            check("示例服务启动", False, "健康检查超时")
            return 1
        check("示例服务启动", True)

        # 让 McpManager 读到这台临时服务（不连 MySQL）
        import src.agent.mcp.server_bean as sb

        class _FakeServers:
            def list_servers(self, include_disabled: bool = False) -> list[dict]:
                return [
                    {"name": "demo", "transport": "streamable_http",
                     "url": f"http://127.0.0.1:{port}/mcp", "headers": {},
                     "display_name": "", "tool_names": {}, "note": "", "enabled": True},
                ]

        sb.mcp_servers = _FakeServers()

        from src.agent.mcp.client import McpManager

        print("[2/3] 发现工具并调用")
        mgr = McpManager()
        tools = mgr.load_tools()
        names = [t.name for t in tools]
        check("工具命名 mcp_demo_get_weather", names == ["mcp_demo_get_weather"], str(names))
        if tools:
            out = asyncio.run(tools[0].ainvoke({"city": "北京"}))
            check("调用返回 stub 数据", "stub" in out and "晴" in out, out[:80])
        probe = mgr.test_server("demo")
        check("探测接口 ok", bool(probe.get("ok")), str(probe)[:120])

        print("[3/3] 一好一坏：坏服务不影响好服务")
        class _Mixed(_FakeServers):
            def list_servers(self, include_disabled: bool = False) -> list[dict]:
                rows = _FakeServers().list_servers()
                rows.append({"name": "broken", "transport": "streamable_http",
                             "url": "http://127.0.0.1:9/mcp", "headers": {},
                             "display_name": "", "tool_names": {}, "note": "", "enabled": True})
                return rows

        sb.mcp_servers = _Mixed()
        mgr2 = McpManager()
        tools2 = [t.name for t in mgr2.load_tools()]
        check("好服务仍装配", tools2 == ["mcp_demo_get_weather"], str(tools2))
        bad = [s for s in mgr2.describe_servers(connect=False) if s["name"] == "broken"]
        check("坏服务有错误原因", bool(bad and bad[0].get("last_error")), str(bad)[:120])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except Exception:
            proc.kill()

    print()
    if FAILED:
        print(f"失败 {len(FAILED)} 项：{', '.join(FAILED)}")
        return 1
    print("MCP 链路端到端：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
