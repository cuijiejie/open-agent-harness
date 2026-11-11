"""嵌入 / 重排：LlamaIndex 的 Embedding 适配器

后端仍然是本地独立进程（`src/kb_embed_server.py`，127.0.0.1:5110）。
为什么不在本进程 import 模型
----------------------------
uvicorn 是 4 个 worker：每个 worker 加载一份 ONNX 模型 ≈ 300MB，四份就 1.2GB，
再加上同机其它服务会顶到内存天花板。独立进程 + 本地 HTTP 只占一份。
（这是部署文档里那条硬约束，别改成进程内加载。）

LlamaIndex 侧只要求实现 `BaseEmbedding` 的那几个方法，
换模型时只改服务端，这里不用动。
"""
from __future__ import annotations

import time

import requests
from llama_index.core.embeddings import BaseEmbedding

from src.infra.utils.LogUtil import log

# 重排服务降级截止时间（0 = 可用）；服务端关掉重排时会 503，没必要每轮都试
_rerank_dead_until: float = 0.0


def _base() -> str:
    from config.Configs import cfg

    return str(getattr(cfg, "kbEmbedUrl", "http://127.0.0.1:5110")).rstrip("/")


def _post(path: str, payload: dict, timeout: float, retries: int = 3) -> dict:
    url = f"{_base()}{path}"
    last: Exception | None = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:                 # 服务刚起/重启时会有瞬时失败
            last = e
            log.warning(f"嵌入服务调用失败（第 {attempt} 次）: {path} {e}")
            time.sleep(0.5 * attempt)
    raise RuntimeError(f"嵌入服务不可用: {last}")


def health() -> dict:
    try:
        r = requests.get(f"{_base()}/health", timeout=3)
        return {"ok": r.status_code == 200, "detail": r.json() if r.ok else r.text[:200]}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


def embed_texts(texts: list[str], batch: int = 32) -> list[list[float]]:
    """批量嵌入；自动分批（一次 32 条，避免请求体过大）"""
    out: list[list[float]] = []
    for i in range(0, len(texts), max(1, batch)):
        part = texts[i:i + batch]
        data = _post("/embed", {"texts": part}, timeout=120)
        vecs = data.get("vectors") or []
        if len(vecs) != len(part):
            raise RuntimeError(f"嵌入返回数量不符：{len(vecs)} != {len(part)}")
        out.extend(vecs)
    return out


def rerank(query: str, docs: list[str], timeout_ms: int = 200) -> list[float] | None:
    """重排打分；超时/失败返回 None（调用方降级为原始顺序）

    服务端把重排关掉时会直接 503。这种情况不该每轮都重试三次（白等 1.5 秒），
    所以失败一次就记 5 分钟「不可用」，期间直接降级。
    """
    global _rerank_dead_until
    if not docs:
        return []
    if time.time() < _rerank_dead_until:
        return None
    try:
        data = _post("/rerank", {"query": query, "documents": docs},
                     timeout=max(0.2, timeout_ms / 1000.0), retries=1)
        scores = data.get("scores") or []
        return [float(s) for s in scores] if len(scores) == len(docs) else None
    except Exception as e:
        _rerank_dead_until = time.time() + 300
        log.warning(f"重排跳过（降级 5 分钟）: {e}")
        return None


class LocalEmbedding(BaseEmbedding):
    """把本地 HTTP 嵌入服务包装成 LlamaIndex 的 Embedding"""

    batch_size: int = 32

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return embed_texts(texts, batch=self.batch_size)

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._embed([query])[0]

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._embed([text])[0]

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_query_embedding(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)

    async def _aget_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        return self._get_text_embeddings(texts)
