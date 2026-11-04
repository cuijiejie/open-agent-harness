"""工具包公共部分：**只保留跨项目通用**的东西

业务系统相关的基址与 HTTP 辅助请放在自己的插件目录里（见 src/plugins/tools/demo）
"""
# ========== 当前登录人（写操作的唯一可信来源） ==========

def current_domain_account() -> str:
    """当前登录人的域账号 —— **只来自登录会话上下文，绝不接受模型/用户传入**

    为什么必须这样：域账号决定产品报备归属与操作记录归属。若把它做成工具入参，
    任何登录用户都能让模型填别人的账号，等于"改数据记到别人名下"。
    会话上下文由 ChatService 每轮开始时 set_sandbox_user(username) 写入，这里只读；
    拿不到就拒绝执行写操作（宁可失败，不猜）。
    """
    try:
        from src.agent.backends.sandbox import get_sandbox_user

        account: str = (get_sandbox_user() or "").strip()
    except Exception:
        account = ""
    if not account:
        raise RuntimeError("无法确定当前登录人（会话上下文缺失），已拒绝执行写操作")
    return account
