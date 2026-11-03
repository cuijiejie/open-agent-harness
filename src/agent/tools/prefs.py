"""用户偏好：两个受权限控制的写/读工具

用户名一律从当前登录会话取（current_domain_account），工具参数里**没有** username，
与写类工具的账号处理保持一致，防止模型代填别人的账号。
"""
from langchain_core.tools import tool

from src.agent.memory import user_prefs as prefs
from src.agent.tools.common import current_domain_account


@tool
def remember_preference(content: str) -> str:
    """记住用户的一条长期**偏好**（只用于"回答方式"，如格式/单位/语气）。

    业务事实一律用 remember_fact，不要用这个工具。

    记住用户的一条长期偏好（跨会话生效）。

    什么时候调用：
    - 用户说"记住…""以后都…""下次…""我一直…"这类明确表达时
    - 用户纠正你的表达方式并希望以后保持（如"以后都用表格回答"）

    什么时候不要调用：
    - 用户没有明确要求记住（不要自己揣测偏好默默写入）
    - 一次性要求（"这次先…"）—— 那是本次对话的要求，不是长期偏好

    参数:
    - content: 偏好正文，一句话说清，例如"回答用表格，不要长篇段落"

    账号由系统自动填充为当前登录人（本工具没有该参数）。
    """
    username = current_domain_account()
    if not username:
        return "拿不到当前登录账号，无法记录偏好"
    ok, msg = prefs.add_pref(username, content)
    return msg


@tool
def forget_preference(content: str) -> str:
    """忘掉用户的一条偏好（按内容模糊匹配）。

    什么时候调用：用户说"忘掉…""不用再…""以后不用…了"时。
    参数:
    - content: 要忘掉的偏好关键词或原文，例如"表格"
    """
    username = current_domain_account()
    if not username:
        return "拿不到当前登录账号，无法操作偏好"
    ok, msg = prefs.del_pref(username, content=content)
    return msg


__all__ = ["remember_preference", "forget_preference"]
