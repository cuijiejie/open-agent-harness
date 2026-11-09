"""知识库（RAG）存储层：PG + pgvector

为什么单独一个库
----------------
知识库的数据量（chunk + 向量）和业务库（MySQL pa_*）完全不同量级，
放一起会让备份/迁移变重；PG 本来就在跑 checkpointer，多一个 kb 库成本为零。

表只有三张：
    kb_doc     文档主表（scope=user/global，sha256 去重）
    kb_chunk   切分后的片段 + 向量 + jieba 分词串
    kb_task    异步索引任务（解析/嵌入/重索引，可重试）

权限口径与 pa_knowledge 一致：scope=user 只看本人，global 全员可读。
检索 SQL 里必须带 scope 条件 —— 不能"先召回再过滤"，否则越权片段会进模型上下文。
"""
from __future__ import annotations

import json
from typing import Any

from src.infra.utils.LogUtil import log

_TABLE_DDL: tuple[str, ...] = (
    "CREATE TABLE IF NOT EXISTS kb_doc ("
    "  id BIGSERIAL PRIMARY KEY,"
    "  scope VARCHAR(16) NOT NULL DEFAULT 'user',"
    "  owner VARCHAR(64) NOT NULL DEFAULT '',"
    "  filename VARCHAR(255) NOT NULL,"
    "  mime VARCHAR(128) NOT NULL DEFAULT '',"
    "  size_kb INT NOT NULL DEFAULT 0,"
    "  sha256 VARCHAR(64) NOT NULL DEFAULT '',"
    "  status VARCHAR(16) NOT NULL DEFAULT 'pending',"
    "  chunks INT NOT NULL DEFAULT 0,"
    "  pages INT NOT NULL DEFAULT 0,"
    "  error TEXT,"
    "  uploader VARCHAR(64) NOT NULL DEFAULT '',"
    "  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
    "  UNIQUE (scope, owner, sha256)"
    ")",
    "CREATE TABLE IF NOT EXISTS kb_chunk ("
    "  id BIGSERIAL PRIMARY KEY,"
    "  doc_id BIGINT NOT NULL REFERENCES kb_doc(id) ON DELETE CASCADE,"
    "  scope VARCHAR(16) NOT NULL DEFAULT 'user',"
    "  owner VARCHAR(64) NOT NULL DEFAULT '',"
    "  seq INT NOT NULL DEFAULT 0,"
    "  content TEXT NOT NULL,"
    "  tokens INT NOT NULL DEFAULT 0,"
    "  page INT,"
    "  section VARCHAR(200) NOT NULL DEFAULT '',"
    "  embedding vector({dim}),"
    "  tokens_text TEXT NOT NULL DEFAULT ''"
    ")",
    "CREATE TABLE IF NOT EXISTS kb_task ("
    "  id BIGSERIAL PRIMARY KEY,"
    "  doc_id BIGINT NOT NULL,"
    "  kind VARCHAR(16) NOT NULL DEFAULT 'index',"
    "  state VARCHAR(16) NOT NULL DEFAULT 'pending',"
    "  attempts INT NOT NULL DEFAULT 0,"
    "  last_error TEXT,"
    "  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ")",
)

_INDEX_DDL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_kb_chunk_scope ON kb_chunk (scope, owner)",
    "CREATE INDEX IF NOT EXISTS idx_kb_chunk_doc ON kb_chunk (doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_kb_task_state ON kb_task (state, updated_at)",
)


def _dsn() -> str:
    from config.Configs import cfg

    return (f"host={cfg.kbPgHost} port={cfg.kbPgPort} dbname={cfg.kbPgDb} "
            f"user={cfg.kbPgUser} password={cfg.kbPgPass}")


_pool = None


def pool():
    """进程内单例连接池（worker 和 web 进程各一份，都是同步短事务）"""
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool

        from config.Configs import cfg

        _pool = ConnectionPool(_dsn(), min_size=1, max_size=int(getattr(cfg, "kbMaxConns", 4)),
                               timeout=10, open=True)
    return _pool


def ensure_schema() -> bool:
    """建表 + 扩展 + 向量索引（幂等）"""
    from config.Configs import cfg

    try:
        with pool().connection() as c:
            c.autocommit = True
            with c.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                for sql in _TABLE_DDL:
                    cur.execute(sql.format(dim=int(getattr(cfg, "kbDim", 768))))
                for sql in _INDEX_DDL:
                    cur.execute(sql)
                # HNSW 索引：建在向量列上，cosine 距离（bge 系列归一化后等价内积）
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_kb_chunk_vec ON kb_chunk "
                    "USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64)"
                )
        return True
    except Exception as e:
        log.error(f"知识库建表失败: {e}", exc_info=True)
        return False


# ---------------- 文档 ----------------

def add_doc(scope: str, owner: str, filename: str, mime: str, size_kb: int,
            sha256: str, uploader: str) -> dict:
    """登记文档；同 scope + owner + sha256 视为同一文件（幂等）"""
    with pool().connection() as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO kb_doc (scope, owner, filename, mime, size_kb, sha256, uploader, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,'pending') "
            "ON CONFLICT (scope, owner, sha256) DO UPDATE SET filename=EXCLUDED.filename "
            "RETURNING id, status",
            (scope, owner, filename, mime, size_kb, sha256, uploader),
        )
        row = cur.fetchone()
        c.commit()
        return {"id": int(row[0]), "status": row[1]}


def get_doc(doc_id: int) -> dict | None:
    with pool().connection() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, scope, owner, filename, mime, size_kb, sha256, status, chunks, pages, "
            "error, uploader, created_at FROM kb_doc WHERE id=%s", (doc_id,))
        r = cur.fetchone()
    if not r:
        return None
    keys = ["id", "scope", "owner", "filename", "mime", "size_kb", "sha256", "status",
            "chunks", "pages", "error", "uploader", "created_at"]
    return dict(zip(keys, r))


def list_docs(scope: str, owner: str, limit: int = 100) -> list[dict]:
    """列出可见文档：个人（本人）+ 全局"""
    with pool().connection() as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, scope, owner, filename, size_kb, status, chunks, pages, error, created_at "
            "FROM kb_doc WHERE scope='global' OR (scope='user' AND owner=%s) "
            "ORDER BY id DESC LIMIT %s",
            (owner, max(1, min(500, limit))),
        )
        rows = cur.fetchall()
    keys = ["id", "scope", "owner", "filename", "size_kb", "status", "chunks", "pages", "error", "created_at"]
    return [dict(zip(keys, r)) for r in rows]


def set_doc_status(doc_id: int, status: str, chunks: int | None = None,
                   pages: int | None = None, error: str | None = None) -> None:
    with pool().connection() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE kb_doc SET status=%s, "
            "chunks=COALESCE(%s, chunks), pages=COALESCE(%s, pages), error=%s WHERE id=%s",
            (status, chunks, pages, error, doc_id),
        )
        c.commit()


def delete_doc(doc_id: int) -> bool:
    with pool().connection() as c, c.cursor() as cur:
        cur.execute("DELETE FROM kb_doc WHERE id=%s", (doc_id,))
        ok = cur.rowcount > 0
        c.commit()
    return ok


def save_chunks(doc_id: int, scope: str, owner: str, chunks: list[dict]) -> int:
    """整篇重建：先删旧 chunk，再批量写入（每块含 content/embedding/tokens_text/service）"""
    with pool().connection() as c, c.cursor() as cur:
        cur.execute("DELETE FROM kb_chunk WHERE doc_id=%s", (doc_id,))
        n = 0
        for ch in chunks:
            vec = ch.get("embedding")
            vec_s = None if vec is None else "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
            cur.execute(
                "INSERT INTO kb_chunk (doc_id, scope, owner, seq, content, tokens, page, section, "
                "embedding, tokens_text) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)",
                (doc_id, scope, owner, ch.get("seq", n), ch["content"], ch.get("tokens", 0),
                 ch.get("page"), ch.get("section", ""), vec_s, ch.get("tokens_text", "")),
            )
            n += 1
        c.commit()
    return n


# ---------------- 任务 ----------------

def add_task(doc_id: int, kind: str = "index") -> int:
    with pool().connection() as c, c.cursor() as cur:
        cur.execute("INSERT INTO kb_task (doc_id, kind) VALUES (%s,%s) RETURNING id", (doc_id, kind))
        tid = int(cur.fetchone()[0])
        c.commit()
    return tid


# 卡住的 running 任务多久后允许被重新抢占（分钟）
_STALE_RUNNING_MIN: int = 10


def next_task() -> dict | None:
    """取一个待处理任务（简单抢占，单 worker 场景够用）

    除 pending/failed 外，还回收「running 但超过 10 分钟没更新」的任务：
    worker 被 kill（部署重启、OOM、客户端超时打断）后任务会永远停在 running，
    不回收的话那份文档再也索引不了 —— 第一次跑就踩到了。
    """
    with pool().connection() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE kb_task SET state='running', attempts=attempts+1, updated_at=now() "
            "WHERE id = (SELECT id FROM kb_task "
            "            WHERE (state IN ('pending','failed') AND attempts < 3) "
            "               OR (state = 'running' "
            "                   AND updated_at < now() - (%s || ' minutes')::interval) "
            "            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, doc_id, kind, attempts",
            (str(_STALE_RUNNING_MIN),),
        )
        r = cur.fetchone()
        c.commit()
    if not r:
        return None
    return {"id": int(r[0]), "doc_id": int(r[1]), "kind": r[2], "attempts": int(r[3])}


def finish_task(task_id: int, ok: bool, error: str = "") -> None:
    with pool().connection() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE kb_task SET state=%s, last_error=%s, updated_at=now() WHERE id=%s",
            ("done" if ok else "failed", (error or "")[:1000], task_id),
        )
        c.commit()


def stats() -> dict:
    with pool().connection() as c, c.cursor() as cur:
        cur.execute("SELECT count(*), coalesce(sum(chunks),0) FROM kb_doc WHERE status='indexed'")
        docs, chunks = cur.fetchone()
        cur.execute("SELECT count(*) FROM kb_doc WHERE status='failed'")
        failed = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM kb_task WHERE state='pending'")
        pending = cur.fetchone()[0]
    return {"docs": int(docs), "chunks": int(chunks), "failed": int(failed), "pending": int(pending)}
