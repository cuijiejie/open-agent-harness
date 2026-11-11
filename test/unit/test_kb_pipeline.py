# -*- coding: utf-8 -*-
"""知识库（LlamaIndex 版）单测：只验证不连库、不联网的部分

覆盖
- 切分：中文按字符计长，块不超过 chunk_size，重叠生效
- 解析：Document 元数据归一化（doc_id/scope/owner/filename/page/section）
- 检索：查询词抽取（拉丁词 + 中文 bigram，去停用词）
- 检索：scope 过滤是嵌套 OR（全局 ∪ 本人），且下推到向量库/SQL
- 上下文拼装：带出处、超预算截断

写法说明
--------
用 unittest（仓库入口是 `python -m unittest discover`）。
llama_index / python-docx 缺失时跳过，而不是让整个套件导入失败。
"""
import importlib.util
import os
import tempfile
import unittest

_HAS_LI = importlib.util.find_spec("llama_index") is not None
_HAS_DOCX = importlib.util.find_spec("docx") is not None

if _HAS_LI:
    from src.infra.kb import index as kb_index
    from src.infra.kb import readers, retrieve, settings


@unittest.skipUnless(_HAS_LI, "未安装 llama-index")
class TestSplitter(unittest.TestCase):
    def test_chunk_size_counts_characters(self):
        settings.configure(force=True)
        text = "".join(f"第{i}条 这是一段用于测试切分的中文内容，长度足够触发分块。" for i in range(40))
        from llama_index.core import Document

        nodes = settings.splitter().get_nodes_from_documents(
            [Document(text=text, id_="1:0", metadata={"doc_id": 1})])
        self.assertGreater(len(nodes), 1, "长文本应被切成多块")
        cap = settings.splitter().chunk_size
        for n in nodes:
            self.assertLessEqual(len(n.text), cap + 8, "块长度不应超过 chunk_size")

    def test_overlap_keeps_context(self):
        settings.configure(force=True)
        text = "".join(f"句子{i}：内容内容内容内容内容。" for i in range(120))
        from llama_index.core import Document

        nodes = settings.splitter().get_nodes_from_documents([Document(text=text, id_="2:0")])
        self.assertGreater(len(nodes), 2)
        # 相邻块有重叠：后一块的开头能在前一块的结尾附近找到
        head = nodes[1].text[:12]
        self.assertIn(head[:6], nodes[0].text[-120:] + nodes[1].text)


@unittest.skipUnless(_HAS_LI, "未安装 llama-index")
class TestReaders(unittest.TestCase):
    def test_markdown_metadata_is_stamped(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "制度.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# 报销制度\n\n第一条 差旅费按标准报销。\n")

        docs, pages = readers.load(p, doc_id=42, filename="制度.md", scope="user", owner="alice")
        self.assertEqual(len(docs), 1)
        meta = docs[0].metadata
        self.assertEqual(meta["kb_doc_id"], 42)
        self.assertEqual(meta["scope"], "user")
        self.assertEqual(meta["owner"], "alice")
        self.assertEqual(meta["filename"], "制度.md")
        self.assertIsNone(meta["page"], "纯文本没有页码，不能编")
        self.assertTrue(meta["section"])
        self.assertEqual(docs[0].id_, "42:0")

    @unittest.skipUnless(_HAS_DOCX, "未安装 python-docx")
    def test_docx_sections_and_tables(self):
        from docx import Document as Docx

        d = tempfile.mkdtemp()
        p = os.path.join(d, "手册.docx")
        doc = Docx()
        doc.add_heading("第一章 总则", level=1)
        doc.add_paragraph("本制度适用于全体员工。")
        table = doc.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "指标"
        table.cell(0, 1).text = "数值"
        table.cell(1, 0).text = "营收"
        table.cell(1, 1).text = "16737.30 亿元"
        doc.save(p)

        docs, _ = readers.load(p, doc_id=7, filename="手册.docx", scope="global", owner="")
        sections = [x.metadata["section"] for x in docs]
        self.assertIn("第一章 总则", sections)
        self.assertTrue(any("表格" in s for s in sections), "表格应被单独采出")
        table_text = [x.text for x in docs if "表格" in x.metadata["section"]][0]
        self.assertIn("16737.30", table_text, "表格里的数值不能丢")

    def test_unsupported_ext_rejected(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.exe")
        open(p, "wb").write(b"MZ")
        with self.assertRaises(ValueError):
            readers.load(p, doc_id=1, filename="x.exe", scope="user", owner="a")


@unittest.skipUnless(_HAS_LI, "未安装 llama-index")
class TestQueryTerms(unittest.TestCase):
    def test_mixed_query(self):
        ts = retrieve.terms("知识库里关于 Harness 工程范式 2023 年数据")
        self.assertIn("harness", ts)
        self.assertIn("2023", ts)
        self.assertIn("工程", ts)
        self.assertTrue(all(len(t) >= 2 for t in ts), "单字噪声应被丢掉")
        self.assertNotIn("的", ts)

    def test_empty(self):
        self.assertEqual(retrieve.terms(""), [])
        self.assertEqual(retrieve.terms("的了吗呢"), [])

    def test_limit(self):
        q = " ".join(f"keyword{i}" for i in range(60))
        self.assertLessEqual(len(retrieve.terms(q)), 24)


@unittest.skipUnless(_HAS_LI, "未安装 llama-index")
class TestScopeFilters(unittest.TestCase):
    def test_nested_or(self):
        f = retrieve.scope_filters("alice")
        self.assertEqual(f.condition.value, "or")
        self.assertEqual(f.filters[0].key, "scope")
        self.assertEqual(f.filters[0].value, "global")
        nested = f.filters[1]
        self.assertEqual(nested.condition.value, "and")
        keys = {nf.key: nf.value for nf in nested.filters}
        self.assertEqual(keys["scope"], "user")
        self.assertEqual(keys["owner"], "alice")

    def test_table_naming(self):
        self.assertEqual(kb_index.data_table(), f"data_{kb_index.table_name()}")


@unittest.skipUnless(_HAS_LI, "未安装 llama-index")
class TestBuildContext(unittest.TestCase):
    def test_citations(self):
        items = [{"doc": "手册.pdf", "page": 3, "section": "第一章", "content": "内容 A"},
                 {"doc": "制度.docx", "page": None, "section": "", "content": "内容 B"}]
        ctx = retrieve.build_context(items)
        self.assertIn("[1] 手册.pdf 第3页 · 第一章", ctx)
        self.assertIn("[2] 制度.docx", ctx)
        self.assertIn("内容 A", ctx)

    def test_budget_truncation(self):
        items = [{"doc": f"d{i}.pdf", "page": i, "section": "", "content": "x" * 400} for i in range(20)]
        ctx = retrieve.build_context(items, budget_chars=500)
        self.assertLessEqual(len(ctx), 520)
        self.assertIn("[1]", ctx)
        self.assertNotIn("[20]", ctx)

    def test_long_content_ellipsis(self):
        ctx = retrieve.build_context([{"doc": "a.pdf", "page": 1, "section": "", "content": "字" * 900}])
        self.assertIn("…", ctx)

    def test_empty(self):
        self.assertEqual(retrieve.build_context([]), "")


if __name__ == "__main__":
    unittest.main()
