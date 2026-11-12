# MCP 天气示例（FastMCP + LangChain）

一台 streamable_http MCP 服务，既做**客户端测试夹具**，也是线上 `pa-mcp-weather` 的实际代码。

## 目录

| 文件 | 作用 |
|---|---|
| `server.py` | 服务本体：LangChain `@tool` 定义工具 → `to_fastmcp` 转换 → `FastMCP` 承载 |
| `check.py` | 离线自检：拉起 stub 服务 → `McpManager` 发现工具 → 调用 → 断言命名与结果，再验"一台坏服务不影响好服务" |

## 为什么这样写（关键）

```
@tool（LangChain 定义工具） → to_fastmcp()（转成 FastMCP 的 Tool） → FastMCP（streamable_http 服务端）
```

- **LangChain 只做客户端**：`langchain-mcp-adapters` 是把 MCP 工具给 agent 用的；服务端它只提供
  `to_fastmcp(tool)` 这一个桥，没有独立的 "LangChain MCP server"。
- **协议由官方 `mcp` SDK 的 `FastMCP` 负责**：会话、SSE、`/mcp` 单端点、协议版本协商都现成，
  不用手写 JSON-RPC（手写只能覆盖 `tools/*`，会话与流式都得自己补）。
- **两个开关**：`stateless_http=True`（每次请求独立，不需要会话头）、`json_response=True`
  （回 `application/json`，工具一问一答不需要 SSE）。
- **一个坑**：`mcp` 1.30 的 `FastMCP.add_tool()` 只接受普通函数，而 `to_fastmcp` 产出的是 `Tool`
  对象（闭包签名是 `**arguments`，按函数注册会丢参数 schema）。所以 `server.py` 里先试
  `add_tool(tool)`，失败就把 `Tool` 直接放进 tool manager。

## 跑自检（不联网、不连库）

```bash
PYTHONPATH=. <env>/bin/python test/mcp_weather/check.py
```

期望 6 个 ✓，最后一行 `MCP 链路端到端：全部通过`。

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

## 协议速查

| 方法 | 由谁处理 |
|---|---|
| `initialize` / `notifications/initialized` | FastMCP（协议版本协商、能力声明） |
| `tools/list` | FastMCP，从 `to_fastmcp` 转来的 Tool 生成 `inputSchema` |
| `tools/call` | FastMCP 调用 LangChain 工具函数，结果转 `content: [{type: "text"}]` |
| `GET /mcp` | FastMCP 的 SSE 推送流（本服务用不上，但已实现） |
| `DELETE /mcp` | FastMCP 结束会话 |
| `GET /health` | 本文件用 `@mcp.custom_route` 加的自定义路由 |
