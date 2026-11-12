# MCP 天气示例（streamable_http）

一个 80 行的 MCP 服务，用来演示「把一个普通 HTTP 能力包成 streamable_http MCP」，
同时充当 MCP 客户端的测试夹具。

## 目录

| 文件 | 作用 |
|---|---|
| `server.py` | 服务本体：`POST /mcp`（initialize / tools/list / tools/call）、`GET /health` |
| `check.py` | 离线自检：拉起 stub 服务 → `McpManager` 发现工具 → 调用 → 断言命名与结果，再验"一台坏服务不影响好服务" |

## 跑自检（不联网、不连库）

```bash
PYTHONPATH=. <env>/bin/python test/mcp_weather/check.py
```

期望输出 6 个 ✓，最后一行 `MCP 链路端到端：全部通过`。

## 手动起服务

```bash
# 真实数据（Open-Meteo，免费无 key）
PYTHONPATH=. <env>/bin/python -m uvicorn test.mcp_weather.server:app --host 127.0.0.1 --port 5120
# 离线桩：返回固定天气，便于断网演示
MCP_WEATHER_STUB=1 PYTHONPATH=. <env>/bin/python -m uvicorn test.mcp_weather.server:app --port 5199
```

线上是 systemd `pa-mcp-weather`（`deploy/pa-mcp-weather.service`，监听 `127.0.0.1:5120`）。

## 接入到平台

页面「技能与工具 → MCP 服务 → 新增服务」：

```
服务名     weather
接入地址   http://127.0.0.1:5120/mcp
请求头     （留空）
```

工具名会变成 `mcp_weather_get_weather`，参数 `city`（中英文城市名都行）。

## 协议要点（自己写 MCP 服务时抄这几条）

| 方法 | 返回 |
|---|---|
| `initialize` | `{"protocolVersion": <客户端传的>, "capabilities": {"tools": {}}, "serverInfo": {...}}` |
| `tools/list` | `{"tools": [{name, description, inputSchema}]}` —— **参数说明写进 inputSchema** |
| `tools/call` | `{"content": [{"type": "text", "text": "..."}], "isError": false}` |
| 通知（无 `id`） | HTTP 202，空 body |

直接返回 `application/json` 即可，不必实现 SSE 流式。
