"""沙箱类工具：在当前用户自己的隔离沙箱内执行命令"""
from langchain_core.tools import tool


# ========== 8. 沙箱执行（每个用户独立的隔离环境） ==========

@tool
def sandbox_shell(command: str) -> str:
    """在当前用户**自己的隔离沙箱**内执行 shell 命令。

    ⚠️ **这是唯一能操作文件、执行命令的工具**：
    凡是涉及「创建/写入/读取/修改/删除文件、列出目录、搜索文件、
    运行脚本或命令」的请求，**必须调用本工具**，把完整命令放进 command 参数。
    **绝不允许**不调用本工具就声称「已创建/已修改/已执行/已搜索到」——
    那是编造结果，会严重误导用户。

    沙箱是独立的隔离环境（内核级约束）：
    - 只能访问沙箱内的文件，**触碰不到服务器文件或其他用户的文件**
    - 网络访问受策略限制（默认禁止出站）
    - 以非 root 身份运行

    适用场景：运行 Python 脚本处理数据、文件/文本操作、数值计算等需要执行命令的任务。
    不适用场景：查询/修改业务系统的数据（请用插件里的专用工具）。

    文件产物请写到 **/sandbox**（工作区），这样用户才能取出来。

    参数:
    - command: 要执行的 shell 命令，如 "python3 -c 'print(1+1)'" 或 "ls -la /sandbox"
    """
    from src.agent.backends.sandbox import sandbox_manager, get_sandbox_user

    username: str = get_sandbox_user()
    if not username:
        return "执行失败：无法确定当前用户身份，请重新登录后再试。"

    if not command or not command.strip():
        return "执行失败：命令为空。"

    result = sandbox_manager.exec(username, command)
    return result.text()


__all__ = ["sandbox_shell"]
