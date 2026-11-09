"""切分：中文友好的定长滑窗（按句子边界断开）

为什么不用 RecursiveCharacterTextSplitter 的默认分隔符
------------------------------------------------------
默认按 "\n\n" / "\n" / " " 切，中文正文常常整段没有换行，结果一块就是几千字；
再按字符硬切又会把"指标 16737.30 亿元"这种断在数字中间。

这里：先按中文句末标点 + 换行切成句子 → 贪心拼到目标长度 → 保留 15% 重叠。
每块都带 page/section，引用时能定位。
"""
from __future__ import annotations

import re

from src.infra.kb.parse import Block

# 中文句末 + 英文句末 + 换行
_SPLIT = re.compile(r"(?<=[。！？；!?;])|(?<=\n)")
_TARGET = 700          # 目标字符数（中文≈350~500 token，留给 top-5 的预算）
_OVERLAP = 0.15


def _jieba_tokens(text: str) -> str:
    """切词结果存库，供 BM25 用（PG 没有中文分词器）"""
    try:
        import jieba

        return " ".join(w for w in jieba.cut_for_search(text) if w.strip())
    except Exception:
        return " ".join(text)


def split_blocks(blocks: list[Block], target: int = _TARGET) -> list[dict]:
    """Block 列表 → chunk 列表（content / page / section / tokens / tokens_text）"""
    chunks: list[dict] = []
    seq = 0
    for b in blocks:
        sentences = [s for s in _SPLIT.split(b.text) if s and s.strip()]
        if not sentences:
            continue
        buf: list[str] = []
        size = 0
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if size + len(s) > target and buf:
                chunks.append(_make(buf, b, seq))
                seq += 1
                # 重叠：保留末尾约 15%
                keep: list[str] = []
                keep_size = 0
                for prev in reversed(buf):
                    if keep_size + len(prev) > int(target * _OVERLAP):
                        break
                    keep.insert(0, prev)
                    keep_size += len(prev)
                buf = keep
                size = keep_size
            buf.append(s)
            size += len(s)
        if buf:
            chunks.append(_make(buf, b, seq))
            seq += 1
    return chunks


def _make(buf: list[str], b: Block, seq: int) -> dict:
    content = "".join(buf).strip()
    # 兜底：库里 text 字段不能有 NUL（解析层已在 _clean 处理，这里防插件/扩展传入脏文本）
    if "\x00" in content:
        content = content.replace("\x00", "")
    return {
        "seq": seq,
        "content": content,
        "page": b.page,
        "section": b.section[:200],
        "tokens": max(1, len(content) // 2),   # 粗估：中文 1 字≈0.5 token
        "tokens_text": _jieba_tokens(content),
    }
