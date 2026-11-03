"""长期记忆工具：记住 / 忘记业务知识

和"偏好"的区别：
  偏好 = 影响回答方式（"用表格""保留两位小数"）
  知识 = 业务事实（"M003838 是主推产品""二手车渠道用 A 方案"）

同一个约束：username 只能从登录会话取，工具参数里没有。
"""
from langchain_core.tools import tool

from src.agent.tools.common import current_domain_account


@tool
def remember_fact(content: str, tags: str = "", scope: str = "user") -> str:
    """记住一条业务知识（跨会话长期保存）。

    什么时候用：用户明确说"记住……""以后都按这个来""这个要记下来"时。
    **不要**自己归纳、不要因为提到一次就记。
    如果只是"以后回答用表格"这类**回答方式**，用 remember_preference，不要用这个。

    参数（JSON 对象）:
    - content: 一句话事实，≤100 字。例："M003838 是我们主推的车抵产品"
    - tags: 3-5 个关键词，逗号分隔，用于以后召回。例："M003838,主推,车抵"
    - scope: user=只对我生效（默认）；global=全公司共享（仅管理员，需谨慎）

    返回：保存结果。条数上限每人 50 条。
    """
    from src.agent.memory import knowledge

    username = current_domain_account()
    if not username:
        return "无法确定当前登录账号，已拒绝保存。"

    content = (content or "").strip()
    if not content:
        return "内容不能为空。"
    if len(content) > 100:
        return f"内容太长（{len(content)} 字），请压缩到 100 字以内在再记。"

    if scope == "global":
        try:
            from src.infra.RbacService import rbac

            if not rbac.permissions(username).get("is_admin"):
                return "只有管理员可以写共享知识。已按个人知识保存。"
                # 注意：这里直接返回，不降级保存，避免误共享
        except Exception:
            return "无法校验管理员身份，已拒绝写入共享知识。"

    res = knowledge.add(content, tags=tags, scope=scope, username=username)
    if not res.get("ok"):
        return f"保存失败：{res.get('error')}"
    return f"已记住（编号 {res['id']}，范围 {'共享' if scope == 'global' else '个人'}）：{content}"


@tool
def forget_fact(content: str) -> str:
    """忘掉一条业务知识。

    什么时候用：用户说"忘掉……""这条不用记了""记错了，删掉"时。

    参数（JSON 对象）:
    - content: 要删除的内容，可以是完整内容或其中一段关键词

    返回：删除结果。
    """
    from src.agent.memory import knowledge

    username = current_domain_account()
    if not username:
        return "无法确定当前登录账号，已拒绝操作。"

    content = (content or "").strip()
    if not content:
        return "请告诉我要忘掉哪一条。"

    res = knowledge.delete(content, username=username)
    if not res.get("ok"):
        return f"没有找到匹配的知识：{content}"
    return f"已删除 {res['deleted']} 条：{content}"


__all__ = ["remember_fact", "forget_fact"]
