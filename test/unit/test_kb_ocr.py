# -*- coding: utf-8 -*-
"""OCR 模块单测：只验证不依赖外部网关的逻辑

覆盖
- ocr.enabled() 的开关判断（base_url 为空时视为未启用）
- 有文字层的页不调模型（省钱）
- 识别结果落盘缓存，第二次不再调用模型
- 全部页失败时抛错（让 ingest 置 failed 而不是静默产出空文档）
"""
import os

import pymupdf
import pytest

from src.infra.kb import ocr as ocr_mod


class _Cfg:
    """替换 config.Configs.cfg 的假配置"""

    def __init__(self, **kw):
        self.kbOcrEnabled = kw.get("enabled", True)
        self.kbOcrBaseUrl = kw.get("base_url", "http://gw.local/v1")
        self.kbOcrApiKey = "k"
        self.kbOcrModel = "m"
        self.kbOcrDpi = 72
        self.kbOcrMaxPages = 30
        self.kbOcrTimeoutS = 5
        self.kbDataDir = kw["data_dir"]


def _make_pdf(path: str, pages: int, with_text: bool) -> None:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if with_text:
            # 文字层要够长（ocr_pdf 判定阈值为 40 字符），否则会被当成扫描页
            page.insert_text((72, 100),
                             f"page {i + 1} text layer content here, long enough for threshold")
    doc.save(path)
    doc.close()


@pytest.fixture()
def fake_cfg(monkeypatch, tmp_path):
    cfg = _Cfg(data_dir=str(tmp_path))
    monkeypatch.setattr("config.Configs.cfg", cfg, raising=False)
    return cfg


def test_disabled_without_base_url(monkeypatch):
    monkeypatch.setattr("config.Configs.cfg", _Cfg(data_dir="/tmp", base_url=""), raising=False)
    assert ocr_mod.enabled() is False


def test_enabled_with_config(fake_cfg):
    assert ocr_mod.enabled() is True


def test_text_layer_page_skips_model(fake_cfg, tmp_path, monkeypatch):
    pdf = str(tmp_path / "t.pdf")
    _make_pdf(pdf, pages=2, with_text=True)

    calls = []
    monkeypatch.setattr(ocr_mod, "_call_vision", lambda png, n: calls.append(n) or "x")
    blocks, total = ocr_mod.ocr_pdf(pdf, doc_id=900)

    assert total == 2
    assert calls == []          # 有文字层，不该调模型
    assert len(blocks) == 2


def test_ocr_result_cached(fake_cfg, tmp_path, monkeypatch):
    pdf = str(tmp_path / "s.pdf")
    _make_pdf(pdf, pages=1, with_text=False)

    calls = []
    monkeypatch.setattr(ocr_mod, "_call_vision", lambda png, n: calls.append(n) or "识别出来的文字")
    blocks1, _ = ocr_mod.ocr_pdf(pdf, doc_id=901)
    blocks2, _ = ocr_mod.ocr_pdf(pdf, doc_id=901)

    assert calls == [1]          # 第二次命中缓存
    assert blocks1[0]["text"] == blocks2[0]["text"] == "识别出来的文字"
    assert blocks1[0]["page"] == 1


def test_all_pages_fail_raises(fake_cfg, tmp_path, monkeypatch):
    pdf = str(tmp_path / "f.pdf")
    _make_pdf(pdf, pages=2, with_text=False)

    def boom(png, n):
        raise RuntimeError("gateway 502")

    monkeypatch.setattr(ocr_mod, "_call_vision", boom)
    with pytest.raises(RuntimeError, match="一页都没识别成功"):
        ocr_mod.ocr_pdf(pdf, doc_id=902)
