# -*- coding: utf-8 -*-
"""BGE-M3 的 ONNX 推理封装（不依赖 torch / sentence-transformers）

为什么自己写
------------
`fastembed` 只支持固定清单里的模型，`BAAI/bge-m3` 不在其中；而官方仓库自带
`onnx/model.onnx` + `onnx/model.onnx_data`，嵌入环境里本来就有
`onnxruntime` 和 `tokenizers`（fastembed 的依赖）。所以直接跑 ONNX，
不必为了一个模型装 torch（CPU wheel 800MB+，内存还要多吃 2~3 GB）。

对外只有 `encode(texts) -> list[list[float]]`：
XLM-RoBERTa tokenizer → **CLS 池化**（bge-m3 官方口径）→ L2 归一化。
bge-m3 不需要 query/passage 前缀。

坑（2026-11-13 实测）：早期这里用的是「均值池化」，bge-m3 会退化成
"什么都很像"——无关中文片段的余弦都在 0.70 上下，排序基本随机，
检索看起来"永远命中一堆不相关内容"。改成取 [CLS] 后区分度恢复正常。
换池化方式必须重建索引（老向量与新向量不兼容）。
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np


class OnnxBgeEncoder:
    """把 bge-m3 的 onnx 目录（model.onnx + model.onnx_data + tokenizer.json）变成编码器"""

    def __init__(self, model_dir: str, max_length: int = 512, threads: int = 4) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.max_length = int(max_length)
        tok_path = os.path.join(model_dir, "tokenizer.json")
        if not os.path.isfile(tok_path):
            raise FileNotFoundError(f"缺少 tokenizer.json: {tok_path}")

        self._tok = Tokenizer.from_file(tok_path)
        self._tok.enable_truncation(max_length=self.max_length)
        # bge-m3 的 padding token 是 <pad>（id 1）；先按 batch 动态 pad，再统一 pad 到最长
        pad_id = 1
        pad_token = "<pad>"
        try:                                  # 优先按 tokenizer_config 里的配置
            import json

            cfg_path = os.path.join(model_dir, "tokenizer_config.json")
            if os.path.isfile(cfg_path):
                with open(cfg_path, encoding="utf-8") as f:
                    cfg = json.load(f)
                pad_token = (cfg.get("pad_token") or {}).get("content") or cfg.get("pad_token") or "<pad>"
                pad_id = int((cfg.get("pad_token_id") or 1))
        except Exception:
            pass
        self._tok.enable_padding(pad_id=pad_id, pad_token=pad_token if isinstance(pad_token, str) else "<pad>")

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, int(threads))
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        model_path = os.path.join(model_dir, "model.onnx")
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"缺少 model.onnx: {model_path}")
        # model.onnx_data 与 model.onnx 同目录时 onnxruntime 会自动加载外部权重
        self._sess = ort.InferenceSession(model_path, opts, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}
        self._output = self._sess.get_outputs()[0].name

    @property
    def dim(self) -> int:
        probe = self.encode(["维度探测"])
        return int(len(probe[0]))

    def encode(self, texts: list[str], batch_size: int = 8) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), max(1, batch_size)):
            part = [t if t.strip() else " " for t in texts[i:i + batch_size]]
            encs = self._tok.encode_batch(part)
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed: dict[str, Any] = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self._sess.run([self._output], {k: v for k, v in feed.items() if k in self._inputs})[0]
            hidden = hidden.astype(np.float32)
            # bge-m3 官方口径是 CLS 池化：last_hidden_state[:, 0]。
            # 均值池化会让向量趋同（无关文本余弦也有 0.7），检索精度明显变差；
            # EMBED_POOLING=mean 可退回旧行为（仅用于对比实验，需重建索引）。
            if os.environ.get("EMBED_POOLING", "cls").strip().lower() == "mean":
                m = mask[..., None].astype(np.float32)
                vecs = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
            else:
                vecs = hidden[:, 0, :]
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            vecs = vecs / np.clip(norms, 1e-9, None)
            out.extend(vecs.tolist())
        return out

    def embed(self, texts: list[str], batch_size: int = 8, **_: Any):
        """与 fastembed 的接口对齐（返回逐条向量），调用方不用区分后端"""
        for v in self.encode(list(texts), batch_size=batch_size):
            yield np.asarray(v, dtype=np.float32)
