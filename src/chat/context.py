"""上下文治理：压缩工具输出 + 滚动摘要（只在轮次边界切，绝不切断 tool/assistant 配对）

为什么需要
----------
治理前存在的三个问题（都已实测确认）：
1. **同一份历史发两遍**：state 里有消息，每轮还把历史拼成 `【对话历史】` 文本前缀塞进
   user 消息 → token 近双倍（前缀注入已在 ChatService 侧改成"仅在 state 为空时"）。
2. **超 200 条从最老整段丢**：早期约定/结论直接消失，没有任何交接说明。
3. **工具输出原样保留**：Excel 解析 JSON 动辄几千字，一轮就吃掉大量预算。

治理策略（每轮对话前跑一次；没超预算时只做一次字符统计，开销可忽略）
------------------------------------------------------------------
① **压工具输出**：早于最近 N 轮的 ToolMessage 内容压成「前 200 字…（已压缩 X 字）」。
   **不删消息** → tool/assistant 配对关系不破（这是当年 deepagents 的
   SummarizationMiddleware 被禁用的根因：它切断了配对，DeepSeek API 直接 400）。
② **仍超预算 → 滚动摘要**：从最老处按【完整轮次】切出一段，交给模型压成 3–5 行要点，
   累加到 `chat_sessions.summary`，然后把这些轮次从 state 里移除。
   摘要失败时**宁可不裁也不丢**（安全优先）；只有极端超限（>2 倍预算）才退回尾部硬切。
③ **摘要注入**：下一轮把 `summary` 作为很短的 `[更早对话的摘要]` 前缀带上，
   它与 state 里的内容不重复。

一句话：**先压缩、再摘要、最后才丢；丢之前一定先交接。**
"""
from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage

from src.infra.utils.LogUtil import log

# 最近 N 轮保持原样（不压工具输出，也不参与摘要）
KEEP_RECENT_ROUNDS: int = 2

# 工具输出压缩后保留的字数（用户确认：前 200 字 + 标注已压缩多少字）
TOOL_HEAD_CHARS: int = 200

# 摘要文本上限（防止摘要本身无限增长）
SUMMARY_MAX_CHARS: int = 4000

# 送进摘要模型的原文上限（防止单次摘要调用过大）
SUMMARY_INPUT_MAX_CHARS: int = 30000

# 极端兜底：压缩+摘要都失败且超过预算的多少倍时，才退回尾部硬切
HARD_CUT_RATIO: float = 2.0


# ---------------------------------------------------------------- #
# 纯函数（可单测，不依赖数据库/模型）
# ---------------------------------------------------------------- #
def msg_chars(msg: Any) -> int:
    """一条消息的字符数（content 可能是 str 或 list）"""
    content: Any = getattr(msg, "content", "") or ""
    return len(content if isinstance(content, str) else str(content))


def total_chars(messages: list[Any]) -> int:
    """一批消息的总字符数"""
    return sum(msg_chars(m) for m in messages)


def round_boundaries(messages: list[Any]) -> list[int]:
    """每条用户消息的下标（= 每轮对话的起点）"""
    return [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]


def _copy_with_content(msg: Any, text: str) -> Any:
    """复制一条消息并替换 content（兼容 pydantic v1/v2）"""
    if hasattr(msg, "model_copy"):
        return msg.model_copy(update={"content": text})
    return msg.copy(update={"content": text})           # type: ignore[attr-defined]


def compress_tool_outputs(
    messages: list[Any],
    keep_rounds: int = KEEP_RECENT_ROUNDS,
    head: int = TOOL_HEAD_CHARS,
) -> tuple[list[Any], int]:
    """压缩「较早轮次」里的工具输出 → (新消息列表, 省下的字符数)

    · 只动 `keep_rounds` 轮之前的 ToolMessage（最近几轮保持完整，模型正在用）
    · 只改 content 文本，**不删消息** → tool/assistant 配对关系不变
    """
    bounds: list[int] = round_boundaries(messages)
    keep_from: int = bounds[-keep_rounds] if len(bounds) >= keep_rounds else 0

    out: list[Any] = []
    saved: int = 0
    for i, m in enumerate(messages):
        if i < keep_from and isinstance(m, ToolMessage):
            text: str = str(m.content or "")
            if len(text) > head:
                new_text: str = text[:head] + f"\n…（已压缩 {len(text) - head} 字）"
                out.append(_copy_with_content(m, new_text))
                saved += len(text) - len(new_text)
                continue
        out.append(m)
    return out, saved


def split_oldest_rounds(
    messages: list[Any],
    budget_chars: int,
    keep_rounds: int = KEEP_RECENT_ROUNDS,
) -> tuple[list[Any], list[Any]]:
    """按预算把最老的若干【完整轮次】切出来 → (老的一段, 保留的一段)

    · 切点永远落在用户消息处，不会把一轮拆开
    · 至少保留 keep_rounds 轮
    · 如果本来就超预算但只有 keep_rounds 轮，则返回 ([], 全部)
    """
    bounds: list[int] = round_boundaries(messages)
    if len(bounds) <= keep_rounds:
        return [], list(messages)

    # 保留段最多到倒数第 keep_rounds 轮
    latest_keep_from: int = bounds[-keep_rounds]
    best_cut: int = latest_keep_from
    for b in bounds:
        if b >= latest_keep_from:
            break
        if total_chars(messages[b:]) <= budget_chars:
            best_cut = b
            break
    return list(messages[:best_cut]), list(messages[best_cut:])


def render_for_summary(messages: list[Any]) -> str:
    """把消息渲染成摘要模型的输入文本（截断到 SUMMARY_INPUT_MAX_CHARS）"""
    lines: list[str] = []
    for m in messages:
        role: str = "用户" if isinstance(m, HumanMessage) else (
            "工具结果" if isinstance(m, ToolMessage) else "助手"
        )
        text: str = str(getattr(m, "content", "") or "")
        text = " ".join(text.split())
        if not text:
            continue
        lines.append(f"{role}：{text[:600]}")
    out: str = "\n".join(lines)
    return out[:SUMMARY_INPUT_MAX_CHARS]


# ---------------------------------------------------------------- #
# 摘要（依赖模型，失败一律降级）
# ---------------------------------------------------------------- #
_SUMMARY_SYSTEM: str = (
    "你是对话摘要器。把给定的一段已结束的中文对话压缩成 3–5 行要点，供后续对话参考。\n"
    "必须保留：① 用户的目标与约束 ② 已确认的关键参数（产品编码/版本/期限/利率/服务费等）\n"
    "③ 已执行的操作及其结果 ④ 尚未完成的待办。\n"
    "只输出摘要正文：不要寒暄、不要标题、不要臆造不存在的事实。"
)


def summarize(
    model: Any,
    old_messages: list[Any],
    prev_summary: str = "",
) -> str:
    """用模型把一段旧对话压成要点；与已有摘要合并。失败抛异常由调用方降级。"""
    body: str = render_for_summary(old_messages)
    if not body.strip():
        return prev_summary

    parts: list[str] = []
    if prev_summary.strip():
        parts.append(f"【已有摘要（请在此基础上合并更新）】\n{prev_summary.strip()}")
    parts.append(f"【本次要压缩的对话】\n{body}")
    prompt: str = "\n\n".join(parts)

    resp: Any = model.invoke([
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": prompt},
    ])
    text: str = str(getattr(resp, "content", "") or "").strip()
    if not text:
        raise RuntimeError("摘要模型返回空内容")
    return text[:SUMMARY_MAX_CHARS]
