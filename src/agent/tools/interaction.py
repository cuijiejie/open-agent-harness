"""交互类工具：需要用户选择或确认时向用户提问"""
from langchain_core.tools import tool


# ========== 5. 向用户提问（需要用户选择时） ==========

@tool
def ask_user(question: str, options: list[str]) -> str:
    """向用户提问并等待用户从给定选项中选择。

    当需要用户做出选择或确认时（例如：多个候选里挑一个、执行前确认参数、
    选择处理方式等），**必须**调用此工具，
    而不是把选项写成普通文字让用户手动输入。

    调用后执行会暂停，前端将渲染可勾选的选项按钮，用户可勾选一个或多个，
    点确认后所选内容会回传给你，你再继续后续操作。

    参数:
    - question: 要问用户的问题（只写问题本身，不要把选项写进去）
    - options: 供用户选择的选项列表，每个元素是用户可读的完整描述
    """
    from langgraph.types import interrupt

    # 将选择请求抛给前端，暂停执行等待用户选择
    # interrupt 返回用户勾选的选项列表（list[str]）
    result = interrupt({"question": question, "options": options})
    if isinstance(result, list):
        return "、".join(str(x) for x in result)
    return str(result)


__all__ = ["ask_user"]
