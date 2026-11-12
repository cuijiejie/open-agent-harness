#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最小 MCP stdio server（仅用于本地验证适配器链路）

实现 initialize / tools/list / tools/call 三个方法，行分隔 JSON-RPC 2.0。
"""
import json
import sys

TOOLS = [{
    "name": "add",
    "description": "两数相加",
    "inputSchema": {
        "type": "object",
        "properties": {"a": {"type": "number", "description": "加数"},
                       "b": {"type": "number", "description": "被加数"}},
        "required": ["a", "b"],
    },
}]


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        continue
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "demo-stdio", "version": "1.0"},
        }})
    elif method == "notifications/initialized":
        continue
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        total = float(args.get("a", 0)) + float(args.get("b", 0))
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": f"{total:g}"}],
            "isError": False,
        }})
    elif mid is not None:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "not found"}})
