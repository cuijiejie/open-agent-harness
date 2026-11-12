# MCP 接入说明

MCP（Model Context Protocol）是"把别人的工具接进 agent"的标准协议。接进来之后，对方的工具会自动出现在模型工具清单里（名字前缀 `mcp_<服务名>_`），不用改代码、不用发版。

本项目用 [`langchain-mcp-adapters`](https://github.com/langchain-ai/langchain-mcp-adapters) 做客户端，**只支持一种传输：`streamable_http`**（MCP 2025-03-26 规范里的远程传输）。

> 为什么不做 stdio：stdio 要在应用机上起进程，等于把"远程执行"开给界面，安全边界守不住；
> 也不做老的 SSE 传输（`GET /sse` + `POST /messages`）——新服务基本不用，多一套只会多一个排障分支。
> 所以本项目里"接一个 MCP 服务"就是**一个 URL**。

---

## 一、接入别人的 MCP 服务（页面，管理员）

入口：**技能与工具 → MCP 服务 → 新增服务**

| 字段 | 说明 |
|---|---|
| 服务名 | 小写字母/数字/`-`/`_`，≤24 字符。它决定工具名前缀：`mcp_<服务名>_<工具名>` |
| 服务中文名 | 界面上显示的名字，留空则显示服务名 |
| 接入地址 | 对方的 MCP 端点，通常是 `https://xxx.com/mcp`（必须以 `http://` 或 `https://` 开头） |
| 请求头 | 需要鉴权就填，**每行一个**：`Authorization: Bearer xxxx` |

保存后：

1. 服务立刻出现在列表里（状态「已连接/未连接」）。
2. 点 **测试** 能看到对方暴露了哪些工具；连不上会显示错误原因。
3. 工具会登记进 `pa_tools`（分组 `MCP·<服务名>`），在「工具管理」和角色页里可以：
   - 勾 **公共** → 所有角色自动拥有（适合只读类工具）；
   - 或按角色逐个勾选 → 只有该角色能用。

> 生效时间：保存会立刻清掉当前 worker 的缓存并重建 agent 实例，其它 worker 的工具缓存是 30 秒 TTL，
> 因此**最多 30 秒**内全量生效（实测新增后下一轮对话即可调用）。

### 举例：接一个公开的 DeepWiki

```
服务名     deepwiki
接入地址   https://mcp.deepwiki.com/mcp
请求头     （留空）
```

接入后会得到三个工具：`mcp_deepwiki_read_wiki_structure`、`mcp_deepwiki_read_wiki_contents`、`mcp_deepwiki_ask_wiki_question`。

### 举例：带 Token 的内部服务

```
服务名     corp-ticket
接入地址   https://mcp.corp.example.com/mcp
请求头     Authorization: Bearer <公司下发的 token>
           X-Env: prod
```

---

## 二、排障

| 现象 | 先看什么 |
|---|---|
| 列表显示「未连接」 | 点「测试」，错误信息会显示在行尾 ⚠ 的 tooltip 里 |
| `连接失败: … 401/403` | 请求头里的 Token 是否过期、是否需要 `X-Api-Key` 之类的自定义头 |
| `连接失败: … timeout` | 对方端点是否可达：在服务器上 `curl -H 'Accept: application/json, text/event-stream' <url>` |
| 工具数为 0 | 对方 `tools/list` 返回空，或该服务只提供 resources/prompts（本项目只用 tools） |
| 改完配置没生效 | 保存会自动清缓存；若在别的 worker 上仍看不到，点「重新连接全部」 |
| 日志关键字 | `MCP 服务已接入`、`MCP 服务连接失败`、`MCP 工具调用失败` |

调用耗时：远程服务每次调用会新建一次 MCP 会话（HTTP 连接），正常在数百毫秒内；单次调用超时由 `[mcp] timeout` 控制（默认 30 秒）。

---

## 三、安全约束（别改）

1. **只有 streamable_http**：接口对其它传输一律 400——不接受任何"在服务器上执行程序"的配置。
2. **只有管理员能增删改**：所有写接口都过 `_is_admin`，并写 `log.warning` 审计（谁、什么服务、什么动作）。
3. **输出截断**：单次工具回传超过 8000 字符会截断，避免把上下文撑爆。
4. **数量上限**：最多 20 个服务、每个服务最多 30 个工具。
5. **信任边界 = 管理员**：远程地址由管理员填写，应用会去请求它（含内网地址）。
   这等价于"管理员可以指定应用访问哪个地址"，不要再把管理员权限发给非运维人员。

---

## 四、代码位置

| 文件 | 作用 |
|---|---|
| `src/agent/mcp/client.py` | 适配器封装：`build_connection`、工具包装、探测、缓存 |
| `src/agent/mcp/server_bean.py` | `pa_mcp_servers` 表结构与 CRUD（`create_server` / `update_server` / `delete_server`） |
| `src/web/routers/mcp.py` | 接口：列表、新增、修改、删除、测试、重载、公共开关 |
| `frontend/src/App.vue` | 「MCP 服务」页（新增/编辑面板、传输徽标、公共开关） |
| `test/unit/test_mcp_client.py` | 连接构建、请求头解析、返回值规整的纯函数测试 |

表结构（`pa_mcp_servers`）：`name` / `transport` / `url` / `headers` / `display_name` / `tool_names` / `note` / `enabled` / `sort_no` / `updated_by` / `updated_at`。
**没有** `command` / `args` / `env`（stdio 已下线，老库启动时会自动删掉这三列）。

---

## 五、把自己的服务包成 MCP

不是所有能力都能在网上找到现成的 MCP 端点。自己包一层很小：官方 `mcp` SDK 的 **FastMCP**
负责协议（会话 / SSE / 单端点），我们只写工具；工具可以直接用 **LangChain 的 `@tool`** 定义，
再用 `to_fastmcp` 转过去。

```python
from langchain_core.tools import tool
from langchain_mcp_adapters.tools import to_fastmcp
from mcp.server.fastmcp import FastMCP

@tool
def get_weather(city: str) -> str:
    """查询城市当前天气"""
    ...

mcp = FastMCP("mcp-weather", stateless_http=True, json_response=True)
t = to_fastmcp(get_weather)
try:
    mcp.add_tool(t)                      # 新版 mcp 支持直接加 Tool
except Exception:
    mcp._tool_manager._tools[t.name] = t  # 1.30 只收函数，Tool 直接入 manager

app = mcp.streamable_http_app()          # 单端点 /mcp，自带会话与 SSE
```

完整可运行示例：`test/mcp_weather/`（数据源 Open-Meteo，免费无 key），
其中 `MCP_WEATHER_STUB=1` 时不联网、返回固定值，供 `check.py` 离线自检。

部署到服务器（已内置 unit：`deploy/pa-mcp-weather.service`，跑的就是这个示例，监听 `127.0.0.1:5120`）：

```bash
cp deploy/pa-mcp-weather.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now pa-mcp-weather
curl -s http://127.0.0.1:5120/health
```

然后在页面「新增服务」填 `http://127.0.0.1:5120/mcp`，点测试能看到 `get_weather`。

改完想验证客户端链路本身有没有坏，离线跑：

```bash
PYTHONPATH=. <env>/bin/python test/mcp_weather/check.py
```
