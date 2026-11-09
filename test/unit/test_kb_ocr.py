# -*- coding: utf-8 -*-
"""OCR 模块单测：只验证不依赖外部网关的逻辑

覆盖
- ocr.enabled() 的开关判断（base_url 为空时视为未启用）
- 有文字层的页不调模型（省钱）
- 识别结果落盘缓存，第二次不再调用模型
- 全部页失败时抛错（让 ingest 置 failed 而不是静默产出空文档）

写法说明
--------
用 **unittest**（不再是 pytest）：仓库的测试入口是 `python -m unittest discover`，
pytest 不在 requirements.txt 里 —— 原先用 pytest.fixture / monkeypatch 写的那版
既跑不起来（缺 pytest），即使装了 pytest 也不会被 unittest 收集（模块级函数不是 TestCase）。
pymupdf 只用来构造 PDF 夹具，缺它就跳过本模块，而不是让整个测试套件导入失败。
"""
import importlib.util
import os
import tempfile
import unittest
from unittest import mock

# pymupdf 只用来构造 PDF 夹具（主服务里是延迟导入）。缺它就跳过本模块，
# 而不是让整个测试套件导入失败；用 find_spec 而不是 try-import，避免把
# 可选依赖写成模块级硬依赖。
_HAS_PYMUPDF: bool = importlib.util.find_spec("pymupdf") is not None

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
    import pymupdf

    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        if with_text:
            # 文字层要够长（ocr_pdf 判定阈值为 40 字符），否则会被当成扫描页
            page.insert_text((72, 100),
                             f"page {i + 1} text layer content here, long enough for threshold")
    doc.save(path)
    doc.close()


@unittest.skipUnless(_HAS_PYMUPDF, "需要 pymupdf（requirements.txt）来构造 PDF 夹具")
class TestKbOcr(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir: str = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self.cfg = _Cfg(data_dir=self.data_dir)
        p = mock.patch("config.Configs.cfg", self.cfg, create=False)
        p.start()
        self.addCleanup(p.stop)

    def _pdf(self, name: str, pages: int, with_text: bool) -> str:
        path = os.path.join(self.data_dir, name)
        _make_pdf(path, pages=pages, with_text=with_text)
        return path

    def test_disabled_without_base_url(self) -> None:
        with mock.patch("config.Configs.cfg", _Cfg(data_dir="/tmp", base_url=""), create=False):
            self.assertFalse(ocr_mod.enabled())

    def test_enabled_with_config(self) -> None:
        self.assertTrue(ocr_mod.enabled())

    def test_text_layer_page_skips_model(self) -> None:
        pdf = self._pdf("t.pdf", pages=2, with_text=True)
        calls: list[int] = []
        with mock.patch.object(ocr_mod, "_call_vision",
                               new=lambda png, n: calls.append(n) or "x"):
            blocks, total = ocr_mod.ocr_pdf(pdf, doc_id=900)

        self.assertEqual(total, 2)
        self.assertEqual(calls, [])          # 有文字层，不该调模型
        self.assertEqual(len(blocks), 2)

    def test_ocr_result_cached(self) -> None:
        pdf = self._pdf("s.pdf", pages=1, with_text=False)
        calls: list[int] = []
        with mock.patch.object(ocr_mod, "_call_vision",
                               new=lambda png, n: calls.append(n) or "识别出来的文字"):
            blocks1, _ = ocr_mod.ocr_pdf(pdf, doc_id=901)
            blocks2, _ = ocr_mod.ocr_pdf(pdf, doc_id=901)

        self.assertEqual(calls, [1])         # 第二次命中缓存
        self.assertEqual(blocks1[0]["text"], "识别出来的文字")
        self.assertEqual(blocks2[0]["text"], "识别出来的文字")
        self.assertEqual(blocks1[0]["page"], 1)

    def test_all_pages_fail_raises(self) -> None:
        pdf = self._pdf("f.pdf", pages=2, with_text=False)

        def boom(png, n):
            raise RuntimeError("gateway 502")

        with mock.patch.object(ocr_mod, "_call_vision", new=boom):
            with self.assertRaises(RuntimeError) as ctx:
                ocr_mod.ocr_pdf(pdf, doc_id=902)
        self.assertIn("一页都没识别成功", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
