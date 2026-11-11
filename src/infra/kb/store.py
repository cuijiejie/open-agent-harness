"""知识库的业务状态表：文档登记 + 索引任务队列（PG）

为什么还留两张自己的表
----------------------
向量和正文交给 LlamaIndex 的 PGVectorStore 管（data_<table>），
但「这份文件是谁传的、多大、sha256、现在索引到哪一步、失败原因」不属于向量库的职责，
也不能塞进 metadata 里（要能按状态轮询、要能算统计）。所以保留两张小表：

    kb_doc    文档主表（scope=user/global，sha256 去重，status 状态机）
    kb_task   异步索引任务（解析/嵌入/重索引，可重试，可回收僵尸任务）

权限口径与 pa_knowledge 一致：scope=user 只看本人，global 全员可读。
检索时的 scope 条件不在这里，而在 retrieve.scope_filters（下推到向量库/关键词 SQL）。
"""
from __future__ import annotations

import os

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
    "CREATE INDEX IF NOT EXISTS idx_kb_doc_scope ON kb_doc (scope, owner)",
    "CREATE INDEX IF NOT EXISTS idx_kb_task_state ON kb_task (state, updated_at)",
)

# 建表串行化用的 advisory lock key（任意常量，只要全仓库一致）
_SCHEMA_LOCK_ID: int = 918273645


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
    """建业务表 + 关键词检索用的 pg_trgm，并让 LlamaIndex 建好向量表（幂等）

    4 个 uvicorn worker 会同时进这里，`CREATE INDEX IF NOT EXISTS` 在 PG 里不是原子的
    —— 并发下会撞 `duplicate key ... already exists`。所以用会话级 advisory lock
    把建表串行化：同一时刻只有一个 worker 在改 schema，其余等它做完再检查一遍。
    """
    try:
        with pool().connection() as c:
            c.autocommit = True
            with c.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(%s)", (_SCHEMA_LOCK_ID,))
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                    for sql in _TABLE_DDL:
                        cur.execute(sql)
                    for sql in _INDEX_DDL:
                        cur.execute(sql)
                    # 向量表/扩展/索引由 PGVectorStore 自己维护；add([]) 触发它的懒建表。
                    # 这一步也必须在锁内：它内部是 CREATE TABLE IF NOT EXISTS，同样扛不住
                    # 4 个 worker 同时进（会撞 pg_type_typname_nsp_index）。
                    from src.infra.kb import index as kb_index

                    kb_index.vector_store().add([])
                finally:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (_SCHEMA_LOCK_ID,))
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
    """删登记 + 删向量 + 删落盘原文件"""
    with pool().connection() as c, c.cursor() as cur:
        cur.execute("DELETE FROM kb_doc WHERE id=%s", (doc_id,))
        ok = cur.rowcount > 0
        c.commit()
    try:
        from src.infra.kb import index as kb_index

        kb_index.delete_doc(doc_id)
    except Exception as e:
        log.warning(f"删除向量失败（登记已删，向量可能残留）: doc={doc_id} {e}")
    try:
        d = _data_dir()
        for fn in os.listdir(d):
            if fn.startswith(f"{doc_id}_"):
                os.remove(os.path.join(d, fn))
    except Exception:
        pass
    return ok


def _data_dir() -> str:
    from config.Configs import cfg

    return str(getattr(cfg, "kbDataDir", "") or os.path.join(os.getcwd(), "data", "kb"))


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
    不回收的话那份文档再也索引不了。
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


def stats(owner: str | None = None) -> dict:
    """统计口径按"调用者能看到什么"算

    不传 owner（后台脚本/worker）时算全库；传 owner 时只算
    `scope=global ∪ (scope=user ∧ owner=本人)` —— 否则普通用户在界面上会看到
    别人的文档数量（徽标显示 1，列表却是空的，很容易被当成 bug）。
    """
    vis = "(scope='global' OR (scope='user' AND owner=%s))"
    with pool().connection() as c, c.cursor() as cur:
        if owner is None:
            cur.execute("SELECT count(*), coalesce(sum(chunks),0) FROM kb_doc WHERE status='indexed'")
        else:
            cur.execute(f"SELECT count(*), coalesce(sum(chunks),0) FROM kb_doc "
                        f"WHERE status='indexed' AND {vis}", (owner,))
        docs, chunks = cur.fetchone()
        if owner is None:
            cur.execute("SELECT count(*) FROM kb_doc WHERE status='failed'")
        else:
            cur.execute(f"SELECT count(*) FROM kb_doc WHERE status='failed' AND {vis}", (owner,))
        failed = cur.fetchone()[0]
        # 任务队列是全局的（一个 worker 服务全库），不按用户切
        cur.execute("SELECT count(*) FROM kb_task WHERE state='pending'")
        pending = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM kb_doc WHERE status='indexed'")
        docs_total = cur.fetchone()[0]
    return {"docs": int(docs), "chunks": int(chunks), "failed": int(failed),
            "pending": int(pending), "docs_total": int(docs_total)}


# ---------------- 迁移 ----------------

def requeue_all() -> int:
    """把所有文档重新排队索引（换切分器 / 换嵌入模型 / 从旧版 RAG 升级后跑一次）"""
    with pool().connection() as c, c.cursor() as cur:
        cur.execute("SELECT id FROM kb_doc ORDER BY id")
        ids = [int(r[0]) for r in cur.fetchall()]
        for i in ids:
            cur.execute("UPDATE kb_doc SET status='pending', chunks=0, error='' WHERE id=%s", (i,))
            cur.execute("INSERT INTO kb_task (doc_id, kind) VALUES (%s,'reindex')", (i,))
        c.commit()
    return len(ids)


def drop_legacy_chunk_table() -> bool:
    """删掉旧版自研 RAG 的 kb_chunk（向量已改由 LlamaIndex 的 data_<table> 承载）"""
    try:
        with pool().connection() as c:
            c.autocommit = True
            with c.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS kb_chunk")
        return True
    except Exception as e:
        log.warning(f"删除旧 kb_chunk 表失败: {e}")
        return False
