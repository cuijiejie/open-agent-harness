#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""本地嵌入 / 重排服务（独立进程，只监听 127.0.0.1）

为什么单独一个进程
------------------
主服务是 uvicorn 4 worker，每个 worker 都 import 一次模型 = 4 份内存（bge-base 约
300MB/份）。这里单独跑一份，主服务通过 HTTP 调用，内存只花一次。

接口：
    GET  /health   → {ok, embedding_model, rerank_model, dim}
    POST /embed    → {"texts": [...]} → {"vectors": [[...], ...], "dim": 768}
    POST /rerank   → {"query": "...", "documents": [...]} → {"scores": [0.1, ...]}

启动：
    python -m src.kb_embed_server --port 5110
模型：
    EMBED_MODEL  默认 sentence-transformers/paraphrase-multilingual-mpnet-base-v2
                 （768 维，多语言；选它是因为 fastembed 0.4 里中文 bge 只有
                  Google Storage 源，内网拉不到；换成有 HF 源的模型才能离线部署）
    RERANK_MODEL 默认 BAAI/bge-reranker-base（不可用时自动降级为不重排）
    HF_ENDPOINT  内网/下载慢时设成 https://hf-mirror.com
"""
from __future__ import annotations

import argparse
import os
import threading
import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

EMBED_MODEL: str = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
RERANK_MODEL: str = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-base")
THREADS: int = int(os.getenv("EMBED_THREADS", "4"))

app = FastAPI(title="OAH KB Embedding Service", docs_url=None, redoc_url=None, openapi_url=None)

_state: dict = {"embed": None, "rerank": None, "dim": 0, "embed_err": "", "rerank_err": "",
                "loaded_at": 0.0}
_lock = threading.Lock()


def _load() -> None:
    """懒加载：启动即加载（systemd 起来就能用），失败记录原因但不退出进程"""
    from fastembed import TextEmbedding

    if _state["embed"] is None:
        try:
            _state["embed"] = TextEmbedding(model_name=EMBED_MODEL, threads=THREADS)
            probe = next(iter(_state["embed"].embed(["向量维度探测"])))
            _state["dim"] = int(len(probe))
            _state["loaded_at"] = time.time()
        except Exception as e:                     # 模型没下载/路径错
            _state["embed_err"] = f"{type(e).__name__}: {e}"

    if _state["rerank"] is None and not _state["rerank_err"]:
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            _state["rerank"] = TextCrossEncoder(model_name=RERANK_MODEL)
        except Exception as e:
            # 重排是可选项：加载失败就明确标记，检索侧会自动跳过
            _state["rerank_err"] = f"{type(e).__name__}: {e}"


class EmbedIn(BaseModel):
    texts: list[str]
    batch: int = 32


class RerankIn(BaseModel):
    query: str
    documents: list[str]


@app.on_event("startup")
def _startup() -> None:
    _load()


@app.get("/health")
def health() -> JSONResponse:
    ok = _state["embed"] is not None
    return JSONResponse({
        "ok": ok,
        "embedding_model": EMBED_MODEL,
        "rerank_model": RERANK_MODEL if _state["rerank"] is not None else "",
        "dim": _state["dim"],
        "embed_error": _state["embed_err"],
        "rerank_error": _state["rerank_err"],
    }, status_code=200 if ok else 503)


@app.post("/embed")
def embed(body: EmbedIn) -> JSONResponse:
    with _lock:
        _load()
    model = _state["embed"]
    if model is None:
        return JSONResponse({"error": f"嵌入模型未加载：{_state['embed_err']}"}, status_code=503)
    texts = [t for t in (body.texts or []) if isinstance(t, str) and t.strip()]
    if not texts:
        return JSONResponse({"vectors": [], "dim": _state["dim"]})
    try:
        vecs = [list(map(float, v)) for v in model.embed(texts, batch_size=body.batch)]
    except Exception as e:
        return JSONResponse({"error": f"嵌入失败：{e}"}, status_code=500)
    return JSONResponse({"vectors": vecs, "dim": _state["dim"], "count": len(vecs)})


@app.post("/rerank")
def rerank(body: RerankIn) -> JSONResponse:
    with _lock:
        _load()
    model = _state["rerank"]
    if model is None:
        return JSONResponse({"error": f"重排模型不可用：{_state['rerank_err']}"}, status_code=503)
    docs = [d for d in (body.documents or []) if isinstance(d, str)]
    if not docs:
        return JSONResponse({"scores": []})
    try:
        scores = [float(s) for s in model.rerank(body.query, docs)]
    except Exception as e:
        return JSONResponse({"error": f"重排失败：{e}"}, status_code=500)
    return JSONResponse({"scores": scores})


def main() -> int:
    import uvicorn

    global EMBED_MODEL
    ap = argparse.ArgumentParser(description="本地嵌入/重排服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5110)
    ap.add_argument("--model", default=EMBED_MODEL)
    args = ap.parse_args()
    EMBED_MODEL = args.model
    uvicorn.run(app, host=args.host, port=args.port, workers=1, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
