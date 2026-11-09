"""摄取管线：上传 → 任务 → 解析 → 切分 → 嵌入 → 入库

为什么用任务表而不是同步处理
----------------------------
一份 27 页 PDF 解析 + 切分 + 嵌入要几十秒到几分钟，放在 HTTP 请求里必然超时；
而且用户上传完就该立刻看到"排队中/索引中"，而不是浏览器转圈。所以：

    上传接口只做三件事：校验、落盘、写 kb_task（毫秒级返回）
    真正的解析/嵌入由 worker 干（夜间 timer 或手动跑）

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

from src.infra.kb import chunk as chunk_mod
from src.infra.kb import embed as embed_cli
from src.infra.kb import parse as parse_mod
from src.infra.kb import store
from src.infra.utils.LogUtil import log

ALLOWED_EXT = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".txt", ".md", ".csv"}


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


def index_doc(doc_id: int) -> dict:
    """真正干活：解析 → 切分 → 嵌入 → 写库"""
    doc = store.get_doc(doc_id)
    if not doc:
        return {"ok": False, "error": f"文档 {doc_id} 不存在"}
    path = _source_path(doc_id)
    if not path:
        store.set_doc_status(doc_id, "failed", error="源文件丢失")
        return {"ok": False, "error": "源文件丢失"}

    store.set_doc_status(doc_id, "parsing")
    try:
        parsed = parse_mod.parse_document(path)
    except parse_mod.NeedOCR as e:
        # 扫描件：没有文字层 → 走 OCR（逐页渲染成图交给视觉模型）
        from src.infra.kb import ocr as ocr_mod

        if not ocr_mod.enabled():
            store.set_doc_status(doc_id, "failed",
                                 error=f"需要 OCR，但未启用（[kb] ocr_enabled / ocr_base_url）：{e}")
            log.warning(f"知识库文档需 OCR 但未启用: doc={doc_id} {e}")
            return {"ok": False, "error": f"该文档是扫描件，OCR 未启用（{e}）"}

        store.set_doc_status(doc_id, "ocr")
        try:
            raw_blocks, total_pages = ocr_mod.ocr_pdf(path, doc_id)
        except Exception as oe:
            store.set_doc_status(doc_id, "failed", error=f"OCR 失败：{oe}"[:500])
            log.error(f"知识库 OCR 失败: doc={doc_id} {oe}", exc_info=True)
            return {"ok": False, "error": f"OCR 失败：{oe}"}
        parsed = parse_mod.Parsed(
            blocks=[parse_mod.Block(**b) for b in raw_blocks], pages=total_pages or 0)
        log.warning(f"OCR 识别完成，继续入库: doc={doc_id} pages={parsed.pages} "
                    f"blocks={len(parsed.blocks)}")
    except Exception as e:
        store.set_doc_status(doc_id, "failed", error=str(e)[:500])
        return {"ok": False, "error": f"解析失败：{e}"}

    chunks = chunk_mod.split_blocks(parsed.blocks)
    if not chunks:
        store.set_doc_status(doc_id, "failed", error="切分后为空")
        return {"ok": False, "error": "切分后为空"}

    store.set_doc_status(doc_id, "embedding")
    try:
        vectors = embed_cli.embed_texts([c["content"] for c in chunks])
    except Exception as e:
        store.set_doc_status(doc_id, "failed", error=f"嵌入失败：{e}")
        return {"ok": False, "error": f"嵌入失败：{e}"}

    for c, v in zip(chunks, vectors):
        c["embedding"] = v
    try:
        n = store.save_chunks(doc_id, doc["scope"], doc["owner"], chunks)
    except Exception as e:
        # 落库失败（如脏字符 / 维度不符）必须把状态改成 failed，
        # 否则文档永远停在 embedding，界面上看着像"卡住了"
        store.set_doc_status(doc_id, "failed", error=f"写库失败：{e}"[:500])
        log.error(f"知识库落库失败: doc={doc_id} {e}", exc_info=True)
        return {"ok": False, "error": f"写库失败：{e}"}
    store.set_doc_status(doc_id, "indexed", chunks=n, pages=parsed.pages, error="")
    log.warning(f"知识库入库完成: doc={doc_id} file={doc['filename']} "
                f"pages={parsed.pages} chunks={n} rev={doc['sha256'][:8]}")
    return {"ok": True, "doc_id": doc_id, "chunks": n, "pages": parsed.pages}


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
    args = ap.parse_args()

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
