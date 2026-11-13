"""检索：LlamaIndex 双路召回（向量 + 关键词）→ RRF 融合 → 本地重排

为什么要混合召回（换库不换策略）
--------------------------------
纯向量对「编号 / 型号 / 年份 / 单位」这类字面特征不敏感：
"天津 2023 年 GDP" 和 "山东 2023 年 GDP" 的向量几乎一样，容易串台；
反过来纯关键词又处理不了"天津经济总量多少"这种口语改写。
所以两路各取 40 条，用 RRF（只看名次，免疫两路分数量纲不同）融合，再交给重排模型定序。

关键词那一路为什么不用 BM25 + jieba
-----------------------------------
jieba 只为切中文查询词，却要多背一份词典依赖；PG 自带 pg_trgm，
用「bigram 命中数 + trigram 相似度」打分，效果接近、依赖为零，还能一条 SQL 完成过滤。
（整个检索只用 PG 一个外部依赖，不再有第二个"词库"要维护。）

权限：**过滤条件在 SQL / vector store 的 metadata filter 里**，
不是召回之后再筛 —— 否则越权片段已经进了模型上下文。
"""
from __future__ import annotations

import re

from llama_index.core.postprocessor.types import BaseNodePostprocessor
from llama_index.core.retrievers import BaseRetriever, QueryFusionRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from llama_index.core.vector_stores import FilterCondition, MetadataFilter, MetadataFilters

from src.infra.kb import embedding
from src.infra.utils.LogUtil import log

RRF_K: int = 60
VEC_TOP: int = 40
KW_TOP: int = 40
FUSE_TOP: int = 20

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.+#-]*|\d+(?:\.\d+)?")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_STOP: set[str] = {"的", "了", "是", "在", "和", "与", "有", "吗", "呢", "么", "怎么", "什么", "哪些", "相关", "内容"}
# 全是这些字的 bigram（"的了""吗呢"）也是噪声，整条丢掉
_STOP_CHARS: set[str] = set("的了吗呢么呀啊吧和与是在有")


def _noise(term: str) -> bool:
    if term in _STOP:
        return True
    return all(ch in _STOP_CHARS for ch in term)


def _cfg(name: str, default):
    from config.Configs import cfg

    return getattr(cfg, name, default)


def terms(query: str, limit: int = 24) -> list[str]:
    """查询 → 关键词（拉丁词 + 中文 bigram），去重去停用词"""
    out: list[str] = []
    for w in _WORD.findall(query or ""):
        w = w.lower().strip("._-+#")
        if len(w) < 2:
            continue
        # 纯数字短语（"99"/"900"）几乎命中一切含数字的表格，噪声远大于收益
        if w.isdigit() and len(w) < 4:
            continue
        out.append(w)
    for run in _CJK.findall(query or ""):
        if len(run) == 1:
            continue
        if _noise(run):
            continue
        out.extend(run[i:i + 2] for i in range(len(run) - 1))
    seen: set[str] = set()
    picked: list[str] = []
    for t in out:
        if _noise(t) or t in seen:
            continue
        seen.add(t)
        picked.append(t)
        if len(picked) >= limit:
            break
    return picked


def scope_filters(owner: str) -> MetadataFilters:
    """可见范围：全局文档 + 本人文档（嵌套 OR，直接下推到 SQL）"""
    return MetadataFilters(
        filters=[
            MetadataFilter(key="scope", value="global"),
            MetadataFilters(
                filters=[
                    MetadataFilter(key="scope", value="user"),
                    MetadataFilter(key="owner", value=owner),
                ],
                condition=FilterCondition.AND,
            ),
        ],
        condition=FilterCondition.OR,
    )


class KeywordRetriever(BaseRetriever):
    """关键词召回：pg_trgm + ILIKE，按「命中词数 → 相似度」排序"""

    def __init__(self, owner: str = "", **kwargs) -> None:
        self._owner = owner
        super().__init__(**kwargs)

    def _retrieve(self, query_bundle: QueryBundle) -> list[NodeWithScore]:
        q = (query_bundle.query_str or "").strip()
        ts = terms(q)
        if not ts:
            return []

        from src.infra.kb import index as kb_index
        from src.infra.kb import store

        like_terms = [f"%{t}%" for t in ts]
        sql = (
            "SELECT node_id, text, metadata_, similarity(text, %s) AS sim "
            f"FROM {kb_index.data_table()} "
            "WHERE (metadata_->>'scope' = 'global' "
            "       OR (metadata_->>'scope' = 'user' AND metadata_->>'owner' = %s)) "
            "  AND (text ILIKE ANY(%s) OR text %% %s) "
            "ORDER BY sim DESC LIMIT 300"
        )
        with store.pool().connection() as c, c.cursor() as cur:
            cur.execute(sql, (q, self._owner, like_terms, q))
            rows = cur.fetchall()

        min_hits: int = int(_cfg("kbKeywordMinHits", 2))
        min_sim: float = float(_cfg("kbKeywordMinSim", 0.15))
        scored: list[tuple[float, tuple]] = []
        for r in rows:
            text = r[1] or ""
            low = text.lower()
            hits = sum(1 for t in ts if t in low)
            if hits == 0:
                continue
            sim = float(r[3] or 0)
            # 只命中一个 bigram 且 trigram 相似度极低 = 噪声（"QA"/"99" 这类通用片段）
            if hits < min_hits and sim < min_sim:
                continue
            scored.append((hits + sim, r))
        scored.sort(key=lambda x: x[0], reverse=True)

        out: list[NodeWithScore] = []
        for score, r in scored[:KW_TOP]:
            meta = dict(r[2] or {})
            # node_id 必须和向量库里的 node_id 一致，否则 RRF 融合时同一片段会被算两条
            node = TextNode(id_=str(r[0] or ""), text=r[1] or "", metadata=meta)
            out.append(NodeWithScore(node=node, score=float(score)))
        return out


class LocalRerank(BaseNodePostprocessor):
    """用本地重排模型重排；超时/不可用就保持原顺序（检索不能因为重排挂掉）"""

    timeout_ms: int = 200

    def _postprocess_nodes(self, nodes: list[NodeWithScore],
                           query_bundle: QueryBundle | None = None) -> list[NodeWithScore]:
        if not nodes or query_bundle is None:
            return nodes
        scores = embedding.rerank(query_bundle.query_str,
                                  [n.node.get_content() for n in nodes],
                                  timeout_ms=self.timeout_ms)
        if not scores:
            return nodes
        for n, s in zip(nodes, scores):
            n.score = float(s)
        return sorted(nodes, key=lambda n: n.score or 0.0, reverse=True)


def _vector_retriever(owner: str):
    from src.infra.kb import index as kb_index

    return kb_index.index().as_retriever(
        similarity_top_k=int(_cfg("kbVecTop", VEC_TOP)),
        filters=scope_filters(owner),
    )


def fusion_retriever(owner: str) -> QueryFusionRetriever:
    return QueryFusionRetriever(
        retrievers=[KeywordRetriever(owner=owner), _vector_retriever(owner)],
        similarity_top_k=int(_cfg("kbFuseTop", FUSE_TOP)),
        mode=FUSION_MODES.RECIPROCAL_RANK,
        num_queries=1,          # 不生成改写查询：不需要 LLM，也省一次调用
        use_async=False,
        retriever_weights=[0.5, 0.5],
    )


def search(query: str, owner: str, top_k: int = 5, use_rerank: bool = True) -> list[dict]:
    """对外入口：返回 [{chunk_id, doc_id, doc, page, section, content, score}]"""
    q = (query or "").strip()
    if not q:
        return []
    from src.infra.kb import settings as kb_settings

    kb_settings.configure()

    nodes = fusion_retriever(owner).retrieve(q)
    if use_rerank and len(nodes) > 1:
        nodes = LocalRerank(timeout_ms=int(_cfg("kbRerankTimeoutMs", 200)))._postprocess_nodes(
            nodes, QueryBundle(query_str=q))

    items: list[dict] = []
    for n in nodes[:max(1, top_k)]:
        meta = n.node.metadata or {}
        items.append({
            "chunk_id": n.node.node_id,
            "doc_id": meta.get("kb_doc_id"),
            "doc": meta.get("filename") or "",
            "page": meta.get("page"),
            "section": meta.get("section") or "",
            "content": n.node.get_content(),
            "score": round(float(n.score or 0.0), 4),
        })
    # 相对阈值：融合分远低于首条的基本是凑数的（RRF 里单路末位也会进榜）
    if len(items) > 1:
        ratio: float = float(_cfg("kbMinScoreRatio", 0.35))
        top: float = float(items[0].get("score") or 0)
        if top > 0 and ratio > 0:
            kept: list[dict] = [x for x in items if float(x.get("score") or 0) >= top * ratio]
            if kept:
                items = kept
    log.info(f"知识库检索: q=<{q[:40]}> 召回={len(nodes)} 返回={len(items)}")
    return items


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
        body = (it.get("content") or "").strip()
        if len(body) > 400:
            body = body[:400] + "…"
        piece = f"[{i}] {loc}\n{body}"
        if used + len(piece) > budget_chars:
            break
        parts.append(piece)
        used += len(piece)
    return "\n\n".join(parts)
