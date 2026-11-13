"""向量索引：LlamaIndex 的 PGVectorStore

表结构由 LlamaIndex 自己维护（不再手写 DDL）：
    <table_name>        文档/节点元数据表
    data_<table_name>   节点正文 + embedding + metadata_ (JSONB)

我们只负责给出「连接参数 + 维度 + HNSW 参数」，其余交给库。
换向量库（Milvus/Qdrant）时改这一个文件即可。
"""
from __future__ import annotations

from typing import Any

from llama_index.core import Document, VectorStoreIndex

from src.infra.utils.LogUtil import log

_store: Any = None


def _cfg(name: str, default):
    from config.Configs import cfg

    return getattr(cfg, name, default)


def table_name() -> str:
    return str(_cfg("kbTableName", "kb_nodes") or "kb_nodes")


def data_table() -> str:
    """LlamaIndex 把节点存在 data_<table> 里（关键词召回要直接查这张表）"""
    return f"data_{table_name()}"


def vector_store():
    """进程内单例；首次构造时自动建表/建扩展/建 HNSW 索引（幂等）"""
    global _store
    if _store is not None:
        return _store

    from llama_index.vector_stores.postgres import PGVectorStore
    from sqlalchemy.engine.url import URL

    def _url(driver: str) -> str:
        """拼连接串：口令里有 @ : / 时也不会解析错"""
        u = URL.create(
            driver,
            username=str(_cfg("kbPgUser", "postgres")),
            password=str(_cfg("kbPgPass", "")),
            host=str(_cfg("kbPgHost", "127.0.0.1")),
            port=int(_cfg("kbPgPort", 5432)),
            database=str(_cfg("kbPgDb", "kb")),
        )
        return u.render_as_string(hide_password=False)

    _store = PGVectorStore(
        # 同步引擎（写入/同步查询）与异步引擎（as_retriever 内部走 async）都要给，
        # 只给 connection_string 时 PGVectorStore 会拿它去建 asyncpg 引擎而解析失败
        connection_string=_url("postgresql+psycopg2"),
        async_connection_string=_url("postgresql+asyncpg"),
        table_name=table_name(),
        embed_dim=int(_cfg("kbDim", 768)),
        use_jsonb=True,          # metadata_ 用 JSONB：中文键值/嵌套过滤都安全
        perform_setup=True,      # 建表 + CREATE EXTENSION vector
        initialization_fail_on_error=True,   # 建不出来就直接报错，别静默降级
        hnsw_kwargs={
            "hnsw_m": 16,
            "hnsw_ef_construction": 64,
            "hnsw_ef_search": 40,
            "hnsw_dist_method": "vector_cosine_ops",   # bge 归一化后余弦=内积
        },
    )
    log.info(f"知识库向量表就绪: {table_name()} / {data_table()}")
    return _store


def index() -> VectorStoreIndex:
    return VectorStoreIndex.from_vector_store(vector_store())


def add_documents(docs: list[Document], on_progress=None) -> int:
    """切分 + 嵌入 + 写入；返回写入的节点数

    嵌入是整条链路里最慢的一步（CPU int8 约 5 秒/512 token），原来一批一批串行发，
    一份 168KB 文档要 40 分钟，还把队列后面所有文档堵住。现在按 kb_embed_concurrency
    并发发若干批（默认 3），顺序仍按原序回填，吞吐提升 2~3 倍。

    on_progress(done, total)：每批完成后回调，用来把「已嵌入块数」写进 kb_doc，
    界面上的片段数会实时增长，用户能看到进度而不是干等。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from llama_index.core import Settings

    from config.Configs import cfg
    from src.infra.kb import settings as kb_settings

    if not docs:
        return 0

    nodes = kb_settings.split_documents(docs)
    if not nodes:
        return 0

    texts: list[str] = [n.get_content() for n in nodes]
    total: int = len(texts)
    batch: int = max(1, int(getattr(cfg, "kbEmbedBatch", 8) or 8))
    conc: int = max(1, min(8, int(getattr(cfg, "kbEmbedConcurrency", 3) or 3)))

    jobs: list[tuple[int, list[str]]] = [(i, texts[i:i + batch]) for i in range(0, total, batch)]
    vectors: list[list[float] | None] = [None] * total
    done: int = 0

    def _embed(part: list[str]) -> list[list[float]]:
        return Settings.embed_model.get_text_embedding_batch(part, show_progress=False)

    with ThreadPoolExecutor(max_workers=min(conc, len(jobs))) as ex:
        futures = {ex.submit(_embed, part): (off, part) for off, part in jobs}
        for fut in as_completed(futures):
            off, part = futures[fut]
            vecs = fut.result()
            if len(vecs) != len(part):
                raise RuntimeError(f"嵌入返回数量不符：{len(vecs)} != {len(part)}")
            for k, vec in enumerate(vecs):
                vectors[off + k] = vec
            done += len(part)
            if on_progress is not None:
                try:
                    on_progress(done, total)
                except Exception as e:
                    log.warning(f"知识库进度回调失败（忽略）: {e}")

    if any(v is None for v in vectors):
        raise RuntimeError("嵌入结果缺失（并发批次数与切块数不一致）")
    for node, vec in zip(nodes, vectors):
        node.embedding = vec
    vector_store().add(nodes)
    return total


def delete_doc(doc_id: int) -> None:
    """整篇删除（重建索引前必须先删，否则会留下旧版本向量）

    按 metadata 里的 doc_id 删，而不是按 ref_doc_id：一个文档会被拆成多个 Document
    （PDF 每页一个），ref_doc_id 是 `<doc_id>:<序号>`，按 doc_id 前缀匹配不可靠。
    """
    from src.infra.kb import store

    try:
        with store.pool().connection() as c, c.cursor() as cur:
            cur.execute(
                f"DELETE FROM {data_table()} WHERE (metadata_->>'kb_doc_id')::bigint = %s",
                (int(doc_id),),
            )
            removed = cur.rowcount
            c.commit()
        if removed:
            log.info(f"知识库删除旧向量: doc={doc_id} nodes={removed}")
    except Exception as e:
        # 文档本来就没索引过（首次入库）时表可能还不存在，不算错误
        log.info(f"知识库删除旧向量跳过: doc={doc_id} {e}")


def reset() -> None:
    """测试用：丢弃单例"""
    global _store
    _store = None
