"""示例插件：演示「不改内核，只加一个目录」就能给平台加工具。

目录约定（被 `src/agent/tools/plugin_loader.py` 自动扫描）：

    src/plugins/tools/demo/
    ├── manifest.json     声明有哪些工具、在哪个文件、叫什么属性
    ├── __init__.py
    └── demo.py           工具实现（用 @tool 装饰器）

注意事项
--------
1. 插件文件里**不允许**出现进程调用、命令执行、动态求值这类危险写法
   （加载器会静态扫描关键字，命中就整模块跳过 —— 这是给业务方开放插件前的最低护栏）。
   需要跑命令时，请通过平台提供的 sandbox_shell 工具，而不是在插件里自己起进程。
2. 工具函数要有 docstring：模型靠它判断什么时候调用。
3. risk 填 read / write / delete：write 类会出现在「能力边界」提示里，
   并且只有被分配了该工具的角色才能调用。
"""
from datetime import datetime

from langchain_core.tools import tool


@tool
def demo_say_hello(name: str = "同学") -> str:
    """向指定的人打招呼。示例工具，用来演示插件注册流程，可以直接删除。"""
    who = (name or "同学").strip()
    return f"你好，{who}！我是通过插件注册进来的工具，不是内核内置的。"


@tool
def demo_server_time(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """返回服务器当前时间。参数 fmt 可选，默认 2026-01-01 12:00:00 这种格式。"""
    try:
        return datetime.now().strftime(fmt or "%Y-%m-%d %H:%M:%S")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
