"""检索：向量 + BM25 混合召回，RRF 融合，可选重排

为什么必须混合
--------------
纯向量对"编号 / 型号 / 年份 / 单位"这类字面特征不敏感：
"天津 2023 年 GDP" 和 "山东 2023 年 GDP" 的向量几乎一样，很容易串台；
反过来纯 BM25 又处理不了"天津经济总量多少"这种口语改写。
所以两路各取 40 条，用 RRF 融合，再让重排模型定最终顺序。

权限：**过滤条件写在 SQL 里**（scope=global 或本人），不是召回后再筛。
"""
from __future__ import annotations

import re

from src.infra.kb import embed as embed_cli
from src.infra.kb import store
from src.infra.utils.LogUtil import log

RRF_K = 60
VEC_TOP = 40
BM25_TOP = 40
FUSE_TOP = 20


def _q_tokens(q: str) -> list[str]:
    try:
        import jieba

        toks = [w.strip() for w in jieba.cut_for_search(q) if w.strip()]
    except Exception:
        toks = [t for t in re.split(r"[\s，。,.;；:：!?？]+", q) if t]
    # 去掉单字停用词，避免"的""是"把候选集撑爆
    stop = {"的", "了", "是", "在", "和", "与", "有", "吗", "呢", "多少", "什么"}
    out = [t for t in toks if t not in stop and len(t) >= 2]
    return out or toks


def _vector_recall(query: str, owner: str, top: int = VEC_TOP) -> list[dict]:
    vec = embed_cli.embed_texts([query])[0]
    vec_s = "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
    sql = (
        "SELECT c.id, c.content, c.page, c.section, d.id, d.filename, "
        "       1 - (c.embedding <=> %s::vector) AS score "
        "FROM kb_chunk c JOIN kb_doc d ON d.id = c.doc_id "
        "WHERE c.embedding IS NOT NULL AND d.status = 'indexed' "
        "  AND (c.scope = 'global' OR (c.scope = 'user' AND c.owner = %s)) "
        "ORDER BY c.embedding <=> %s::vector LIMIT %s"
    )
    with store.pool().connection() as c, c.cursor() as cur:
        cur.execute(sql, (vec_s, owner, vec_s, top))
        rows = cur.fetchall()
    return [{"chunk_id": r[0], "content": r[1], "page": r[2], "section": r[3],
             "doc_id": r[4], "doc": r[5], "score": float(r[6] or 0)} for r in rows]


def _bm25_recall(query: str, owner: str, top: int = BM25_TOP) -> list[dict]:
    from rank_bm25 import BM25Okapi

    terms = _q_tokens(query)
    if not terms:
        return []
    likes = [f"%{t}%" for t in terms]
    sql = (
        "SELECT c.id, c.content, c.page, c.section, d.id, d.filename, c.tokens_text "
        "FROM kb_chunk c JOIN kb_doc d ON d.id = c.doc_id "
        "WHERE d.status = 'indexed' "
        "  AND (c.scope = 'global' OR (c.scope = 'user' AND c.owner = %s)) "
        "  AND (c.tokens_text ILIKE ANY(%s) OR c.content ILIKE %s) "
        "LIMIT 300"
    )
    with store.pool().connection() as c, c.cursor() as cur:
        cur.execute(sql, (owner, likes, f"%{query}%"))
        rows = cur.fetchall()
    if not rows:
        return []
    corpus = [(r[6] or r[1] or "").split() for r in rows]
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(terms)
    idx = sorted(range(len(rows)), key=lambda i: scores[i], reverse=True)[:top]
    out = []
    for i in idx:
        if scores[i] <= 0:
            continue
        r = rows[i]
        out.append({"chunk_id": r[0], "content": r[1], "page": r[2], "section": r[3],
                    "doc_id": r[4], "doc": r[5], "score": float(scores[i])})
    return out


def _rrf(lists: list[list[dict]], top: int = FUSE_TOP) -> list[dict]:
    """Reciprocal Rank Fusion：只依赖名次，天然免疫两路分数量纲不同"""
    merged: dict[int, dict] = {}
    for lst in lists:
        for rank, item in enumerate(lst):
            hit = merged.setdefault(item["chunk_id"], {**item, "rrf": 0.0})
            hit["rrf"] += 1.0 / (RRF_K + rank + 1)
    return sorted(merged.values(), key=lambda x: x["rrf"], reverse=True)[:top]


def search(query: str, owner: str, top_k: int = 5, use_rerank: bool = True) -> list[dict]:
    """对外入口：返回 [{content, doc, page, section, score}]"""
    q = (query or "").strip()
    if not q:
        return []
    vec_hits = _vector_recall(q, owner)
    bm_hits = _bm25_recall(q, owner)
    fused = _rrf([vec_hits, bm_hits])
    if not fused:
        return []

    if use_rerank and len(fused) > 1:
        from config.Configs import cfg

        scores = embed_cli.rerank(q, [h["content"] for h in fused],
                                  timeout_ms=int(getattr(cfg, "kbRerankTimeoutMs", 200)))
        if scores:
            for h, s in zip(fused, scores):
                h["rerank"] = s
            fused.sort(key=lambda x: x.get("rerank", 0), reverse=True)

    out = []
    for h in fused[:max(1, top_k)]:
        out.append({
            "chunk_id": h["chunk_id"], "doc_id": h["doc_id"], "doc": h["doc"],
            "page": h["page"], "section": h.get("section") or "",
            "content": h["content"], "score": round(float(h.get("rerank", h["rrf"])), 4),
        })
    log.info(f"知识库检索: q=<{q[:40]}> vec={len(vec_hits)} bm25={len(bm_hits)} "
             f"fused={len(fused)} 返回={len(out)}")
    return out


def build_context(items: list[dict], budget_chars: int = 2500) -> str:
    """把检索结果拼成给模型的上下文（带出处，超预算就截断）"""
    if not items:
        return ""
    parts: list[str] = []
    used = 0
    for i, it in enumerate(items, 1):
        loc = f"{it['doc']}"
        if it.get("page"):
            loc += f" 第{it['page']}页"
        if it.get("section"):
            loc += f" · {it['section']}"
        body = it["content"].strip()
        if len(body) > 400:
            body = body[:400] + "…"
        piece = f"[{i}] {loc}\n{body}"
        if used + len(piece) > budget_chars:
            break
        parts.append(piece)
        used += len(piece)
    return "\n\n".join(parts)
