"""摄取管线：上传 → 任务 → 解析（LlamaIndex readers）→ 切分 → 嵌入 → 向量库

为什么用任务表而不是同步处理
----------------------------
一份 27 页 PDF 解析 + 切分 + 嵌入要几十秒到几分钟，放在 HTTP 请求里必然超时；
而且用户上传完就该立刻看到"排队中/索引中"，而不是浏览器转圈。所以：

    上传接口只做三件事：校验、落盘、写 kb_task（毫秒级返回）
    真正的解析/切分/嵌入由 worker 干（上传后立刻踢一脚，见 kick.py）

用法：
    python -m src.infra.kb.ingest --once        # 处理完队列里所有任务
    python -m src.infra.kb.ingest --loop 60     # 常驻，每 60 秒轮询
"""
from __future__ import annotations

import argparse
import hashlib
import os
import time
import uuid

from src.infra.kb import index as kb_index
from src.infra.kb import readers
from src.infra.kb import store
from src.infra.utils.LogUtil import log

ALLOWED_EXT: set[str] = readers.ALLOWED_EXT


def _data_dir() -> str:
    from config.Configs import cfg

    d = str(getattr(cfg, "kbDataDir", "") or os.path.join(os.getcwd(), "data", "kb"))
    os.makedirs(d, exist_ok=True)
    return d


def _max_mb() -> int:
    from config.Configs import cfg

    return int(getattr(cfg, "kbMaxFileMb", 20))


def save_upload(scope: str, owner: str, filename: str, data: bytes,
                mime: str = "", uploader: str = "") -> dict:
    """校验 + 落盘 + 登记 + 建任务（不解析）"""
    name = os.path.basename(filename or "unnamed")
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        return {"ok": False, "error": f"不支持的类型 {ext}；支持：{', '.join(sorted(ALLOWED_EXT))}"}
    size_mb = len(data) / 1024 / 1024
    if size_mb > _max_mb():
        return {"ok": False, "error": f"文件超过 {_max_mb()}MB（当前 {size_mb:.1f}MB）"}
    if not data:
        return {"ok": False, "error": "空文件"}

    sha = hashlib.sha256(data).hexdigest()
    doc = store.add_doc(scope, owner if scope == "user" else "", name, mime,
                        max(1, len(data) // 1024), sha, uploader or owner)
    doc_id = doc["id"]

    path = os.path.join(_data_dir(), f"{doc_id}_{uuid.uuid4().hex[:8]}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    store.set_doc_status(doc_id, "pending", error="")
    task_id = store.add_task(doc_id, "index")
    log.warning(f"知识库上传: doc={doc_id} file={name} size={size_mb:.1f}MB "
                f"scope={scope} owner={owner} task={task_id}")
    return {"ok": True, "doc_id": doc_id, "task_id": task_id, "status": "pending"}


def _source_path(doc_id: int) -> str | None:
    d = _data_dir()
    for fn in os.listdir(d):
        if fn.startswith(f"{doc_id}_"):
            return os.path.join(d, fn)
    return None


def _load_with_ocr(path: str, doc: dict) -> tuple[list, int]:
    """PDF 无文字层时走 OCR；其余格式解析失败直接抛错"""
    from src.infra.kb import ocr as ocr_mod

    try:
        return readers.load(path, doc_id=doc["id"], filename=doc["filename"],
                            scope=doc["scope"], owner=doc["owner"])
    except readers.NeedOCR as e:
        if not ocr_mod.enabled():
            raise RuntimeError(f"该文档是扫描件，OCR 未启用（{e}）") from e
        store.set_doc_status(doc["id"], "ocr")
        blocks, total_pages = ocr_mod.ocr_pdf(path, doc["id"])
        docs = readers.ocr_documents(blocks, doc_id=doc["id"], filename=doc["filename"],
                                     scope=doc["scope"], owner=doc["owner"])
        log.warning(f"OCR 识别完成，继续入库: doc={doc['id']} pages={total_pages} "
                    f"blocks={len(blocks)}")
        return docs, total_pages or 0


def index_doc(doc_id: int) -> dict:
    """真正干活：解析 → 切分 → 嵌入 → 写向量库（LlamaIndex 负责后三步）"""
    doc = store.get_doc(doc_id)
    if not doc:
        return {"ok": False, "error": f"文档 {doc_id} 不存在"}
    path = _source_path(doc_id)
    if not path:
        store.set_doc_status(doc_id, "failed", error="源文件丢失")
        return {"ok": False, "error": "源文件丢失"}

    store.set_doc_status(doc_id, "parsing")
    try:
        docs, pages = _load_with_ocr(path, doc)
    except Exception as e:
        store.set_doc_status(doc_id, "failed", error=str(e)[:500])
        log.error(f"知识库解析失败: doc={doc_id} {e}", exc_info=True)
        return {"ok": False, "error": f"解析失败：{e}"}

    store.set_doc_status(doc_id, "embedding")
    try:
        kb_index.delete_doc(doc_id)          # 重建索引：先清旧向量，否则会留重复
        n = kb_index.add_documents(docs)
    except Exception as e:
        store.set_doc_status(doc_id, "failed", error=f"写入向量库失败：{e}"[:500])
        log.error(f"知识库写入失败: doc={doc_id} {e}", exc_info=True)
        return {"ok": False, "error": f"写入向量库失败：{e}"}

    if n == 0:
        store.set_doc_status(doc_id, "failed", error="切分后为空")
        return {"ok": False, "error": "切分后为空"}

    store.set_doc_status(doc_id, "indexed", chunks=n, pages=pages, error="")
    log.warning(f"知识库入库完成: doc={doc_id} file={doc['filename']} "
                f"pages={pages} nodes={n} rev={doc['sha256'][:8]}")
    return {"ok": True, "doc_id": doc_id, "chunks": n, "pages": pages}


def run_once() -> int:
    """处理队列；返回处理条数"""
    store.ensure_schema()
    n = 0
    while True:
        task = store.next_task()
        if not task:
            break
        try:
            if task["kind"] == "reindex":
                store.set_doc_status(task["doc_id"], "pending")
            res = index_doc(task["doc_id"])
            store.finish_task(task["id"], bool(res.get("ok")), str(res.get("error") or ""))
        except Exception as e:
            store.finish_task(task["id"], False, str(e)[:500])
            log.error(f"知识库任务失败: task={task['id']} {e}", exc_info=True)
        n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库索引 worker")
    ap.add_argument("--once", action="store_true", help="处理完队列即退出")
    ap.add_argument("--loop", type=int, default=0, help="常驻：每 N 秒轮询一次")
    ap.add_argument("--reindex-all", action="store_true",
                    help="升级/换模型后一次性重建：删旧 kb_chunk 表，把所有文档重新排队")
    args = ap.parse_args()

    if args.reindex_all:
        store.ensure_schema()
        store.drop_legacy_chunk_table()
        n = store.requeue_all()
        print(f"已删除旧 kb_chunk 表，重新排队 {n} 个文档")
        done = run_once()
        print(f"重建完成：{done} 个任务")
        return 0

    if args.once or not args.loop:
        n = run_once()
        print(f"处理完成：{n} 个任务")
        return 0
    log.warning(f"知识库 worker 常驻启动，轮询间隔 {args.loop}s")
    while True:
        try:
            n = run_once()
            if n:
                log.warning(f"worker 本轮处理 {n} 个任务")
        except Exception as e:
            log.error(f"worker 循环异常: {e}", exc_info=True)
        time.sleep(max(5, args.loop))


if __name__ == "__main__":
    raise SystemExit(main())
