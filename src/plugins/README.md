# src/plugins —— 插件目录

判断标准很简单：**换个项目还要不要用。**

| 位置 | 放什么 | 判断标准 | 例子 |
|---|---|---|---|
| `src/agent/tools/` | 功能性内置工具 | 换项目还用得上 | `ask_user`、`sandbox_shell`、记忆读写 |
| `src/agent/mcp/` | MCP 客户端框架 | 换项目还用得上 | streamable_http 客户端、工具登记 |
| **`src/plugins/tools/`** | **你自己的业务工具** | 只服务你的业务系统 | `demo/`（示例） |
| **`src/plugins/mcp/`** | **你自己的 MCP 服务** | 只服务你的业务系统 | 自行部署一个 streamable_http 端点，再在页面接入 |

## 加一个业务工具（不用改内核）

```
src/plugins/tools/hello/
├── manifest.json   声明工具名 / 模块 / risk / 中文名
├── hello.py        @tool 函数（docstring 会被模型用来判断何时调用）
└── __init__.py
```

`manifest.json`：

```json
{
  "name": "hello",
  "display_name": "示例插件",
  "version": "1.0.0",
  "tools": [
    {
      "name": "hello_say",
      "module": "hello",
      "attr": "hello_say",
      "risk": "read",
      "group": "示例",
      "display_name": "打个招呼",
      "note": "可删除"
    }
  ]
}
```

规则：

1. `risk` 填 `read` / `write` / `delete`：写类工具会出现在「能力边界」提示里，
   只有被分配了该工具的角色才能调用；
2. 插件文件里**不许出现** `subprocess`、`os.system`、`os.popen`、`eval(`、`exec(`
   —— 加载器会静态扫描，命中就整模块跳过；
3. 加载失败只影响这一个插件，不会让服务起不来（日志搜 `[plugins]`）。

## 目录约定

```
tools/<业务域>/
  manifest.json     工具声明
  <module>.py       工具实现
  README.md         这个业务域的说明（可选）
```
