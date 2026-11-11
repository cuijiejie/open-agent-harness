"""知识库检索工具（RAG）

和 remember_fact / knowledge 的区别：
    remember_fact  用户明确说的"记住……"，短事实，每轮注入
    kb_search      上传的文档（PDF/Word/Excel/PPT），按需检索，必须给出处

约束同其它工具：username 只从登录上下文取，参数里没有。
"""
from __future__ import annotations

from langchain_core.tools import tool

from src.agent.tools.common import current_domain_account


@tool
def kb_search(query: str, top_k: int = 5) -> str:
    """在知识库里检索文档内容（制度文件、产品手册、统计公报、历史报告）。

    什么时候用：用户问的细节可能写在已上传的文档里，而你不确定答案时。
    **不要**用它回答常识问题，也别拿它当联网搜索（知识库只有内部文档）。

    参数（JSON 对象）:
    - query: 检索式，1~3 个关键词或一句具体问题。例："天津 2023 地区生产总值"
    - top_k: 返回片段数，默认 5，最多 8

    返回：带出处的片段（文档名 + 页码 + 小节）。如果什么都没检索到，
    如实告诉用户"知识库里没有相关内容"，不要编。
    """
    username = current_domain_account()
    if not username:
        return "无法确定当前登录账号，已拒绝检索。"

    q = (query or "").strip()
    if not q:
        return "检索内容不能为空。"
    try:
        k = max(1, min(8, int(top_k or 5)))
    except (TypeError, ValueError):
        k = 5

    try:
        from src.infra.kb import retrieve as kb
        from config.Configs import cfg

        if not bool(getattr(cfg, "kbEnabled", True)):
            return "知识库未启用（[kb] enabled=false）。"

        items = kb.search(q, username, top_k=k)
        if not items:
            return (f"知识库中没有检索到与「{q}」相关的内容。"
                    "请如实告诉用户没查到，不要推测。")

        ctx = kb.build_context(items, budget_chars=2500)
        head = f"知识库检索到 {len(items)} 段相关内容（引用时请标明文档名和页码）：\n\n"
        return head + ctx
    except Exception as e:
        from src.infra.utils.LogUtil import log

        log.error(f"kb_search 失败: {e}", exc_info=True)
        return f"知识库检索失败：{type(e).__name__}。请如实告知用户检索不可用，不要编造内容。"
