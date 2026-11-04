"""工具包：按域分模块，统一从这里导出 ALL_TOOLS

## 新增工具怎么加

1. 判断属于哪一类（数据 / 文件 / 沙箱 / 交互），
   在对应模块里用 `@tool` 定义函数；都不属于就新建一个模块文件。
   业务自己的工具建议放 `src/plugins/tools/<name>/`，不用改这里。
2. 在下面 `ALL_TOOLS` 里加一行（顺序即给模型的工具清单顺序）。
3. 新工具要对 agent 生效，还需同步更新：
   - `middleware/FixToolNameMiddleware.py` 的 `_VALID_TOOLS`（工具名白名单）
   - `AGENT.md` 的使用说明（告诉模型什么时候该用）
   - 若涉及幻觉风险，参考 `ChatService._FABRICATION_ACTIONS` 的兜底校验

## 为什么拆包

原先是单文件 `service/ToolDefinitions.py`（426 行、8 个工具挤在一起），
工具一多就难以维护（改一个工具要在一个大文件里翻找，diff 也噪音大）。
按业务域拆开后：加/改工具只动一个文件 + 这里一行，互不干扰。

## 模块划分

    common.py            公共工具函数（HTTP 调用、参数校验等）
    demo/                示例插件：演示如何注册自己的业务工具
    sandbox.py           沙箱：sandbox_shell
    interaction.py       交互：ask_user
"""
from src.agent.tools.interaction import ask_user
from src.agent.tools.sandbox import sandbox_shell

from src.agent.tools.registry import REGISTRY, ToolSpec, describe, resolve_tools

# 兼容旧接口：**不带用户上下文**的完整清单（只用于排障/文档）。
# 真实装配请用 `resolve_tools(username)` —— 它会按授权过滤（没权限的工具不进模型清单）。
# ALL_TOOLS 由 REGISTRY 派生＝内置工具 + src/plugins/ 下的业务插件
# （以前是手写清单，新增工具忘了加会导致"模型看不到"，2026-10-22 踩过）
ALL_TOOLS: list = [spec.tool for spec in REGISTRY]

__all__ = ["ALL_TOOLS", "REGISTRY", "ToolSpec", "resolve_tools", "describe"]
