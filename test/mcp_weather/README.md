# MCP 天气示例（FastMCP）

一台 streamable_http MCP 服务，既做**客户端测试夹具**，也是线上 `pa-mcp-weather` 的实际代码。

## 目录

| 文件 | 作用 |
|---|---|
| `server.py` | 服务本体：`FastMCP` + `@mcp.tool()`，docstring = 工具说明，类型注解 = inputSchema |
| `check.py` | 离线自检：拉起 stub 服务 → `McpManager` 发现工具 → 调用 → 断言命名与结果，再验"一台坏服务不影响好服务" |

## 为什么这样写（关键）

```
@mcp.tool()（FastMCP 原生）  →  FastMCP 承载 streamable_http  →  app = mcp.streamable_http_app()
```

- **服务端只做一件事**：把函数暴露成 MCP 工具。FastMCP 的 `@mcp.tool()` 就是这个，
  docstring 自动成为工具说明，类型注解自动成为 `inputSchema`。
- **不需要 LangChain 转一手**：`@tool` → `to_fastmcp` → 塞进 tool manager 只是多一层间接。
  LangChain 在本项目里的位置是**客户端**（`langchain-mcp-adapters`）；
  只有要复用某个**已存在的 LangChain 工具实现**时，才用 `to_fastmcp` 那个桥。
- **协议全部由 `mcp` 官方 SDK 负责**：`/mcp` 单端点、会话、SSE、协议版本协商都现成。
- **两个开关**：`stateless_http=True`（每次请求独立，不要会话头）、`json_response=True`
  （回 `application/json`，工具一问一答不需要 SSE）。

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
| `tools/list` | FastMCP，从 `@mcp.tool()` 的 docstring 与类型注解生成 `inputSchema` |
| `tools/call` | FastMCP 调用工具函数，结果转 `content: [{type: "text"}]` |
| `GET /mcp` | FastMCP 的 SSE 推送流（本服务用不上，但已实现） |
| `DELETE /mcp` | FastMCP 结束会话 |
| `GET /health` | 本文件用 `@mcp.custom_route` 加的自定义路由 |
