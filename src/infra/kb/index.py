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

    _store = PGVectorStore.from_params(
        database=str(_cfg("kbPgDb", "kb")),
        host=str(_cfg("kbPgHost", "127.0.0.1")),
        port=str(int(_cfg("kbPgPort", 5432))),
        user=str(_cfg("kbPgUser", "postgres")),
        password=str(_cfg("kbPgPass", "")),
        table_name=table_name(),
        embed_dim=int(_cfg("kbDim", 768)),
        use_jsonb=True,          # metadata_ 用 JSONB：中文键值/嵌套过滤都安全
        perform_setup=True,      # 建表 + CREATE EXTENSION vector
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


def add_documents(docs: list[Document]) -> int:
    """切分 + 写入；返回写入的节点数"""
    from src.infra.kb import settings as kb_settings

    nodes = kb_settings.splitter().get_nodes_from_documents(docs)
    if not nodes:
        return 0
    vector_store().add(nodes)
    return len(nodes)


def delete_doc(doc_id: int) -> None:
    """整篇删除（重建索引前必须先删，否则会留下旧版本向量）

    按 metadata 里的 doc_id 删，而不是按 ref_doc_id：一个文档会被拆成多个 Document
    （PDF 每页一个），ref_doc_id 是 `<doc_id>:<序号>`，按 doc_id 前缀匹配不可靠。
    """
    from src.infra.kb import store

    try:
        with store.pool().connection() as c, c.cursor() as cur:
            cur.execute(
                f"DELETE FROM {data_table()} WHERE (metadata_->>'doc_id')::bigint = %s",
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
