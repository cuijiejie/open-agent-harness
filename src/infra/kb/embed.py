"""嵌入 / 重排客户端：调本地独立进程（127.0.0.1）

为什么走 HTTP 而不是在本进程 import fastembed
---------------------------------------------
uvicorn 是 4 个 worker：每个 worker 加载一份 ONNX 模型 ≈ 300MB，四份就 1.2GB，
再加上 15G 机器的其它服务会顶到天花板。独立进程 + 本地 HTTP 只占一份。
（这是设计文档里那条"硬约束"，别改成进程内加载。）
"""
from __future__ import annotations

import time

import requests

from src.infra.utils.LogUtil import log


def _base() -> str:
    from config.Configs import cfg

    return str(getattr(cfg, "kbEmbedUrl", "http://127.0.0.1:5110")).rstrip("/")


def _post(path: str, payload: dict, timeout: float) -> dict:
    url = f"{_base()}{path}"
    last: Exception | None = None
    for attempt in range(1, 4):
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
    """重排打分；超时/失败返回 None（调用方降级为原始顺序）"""
    if not docs:
        return []
    try:
        data = _post("/rerank", {"query": query, "documents": docs},
                     timeout=max(0.2, timeout_ms / 1000.0))
        scores = data.get("scores") or []
        return [float(s) for s in scores] if len(scores) == len(docs) else None
    except Exception as e:
        log.warning(f"重排跳过（降级）: {e}")
        return None
