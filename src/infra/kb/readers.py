"""文档解析：把 PDF / DOCX / XLSX / PPTX / TXT 变成 LlamaIndex 的 Document

用 LlamaIndex 的 readers 而不是自己写解析：
    PDF   PDFReader（pymupdf 底层，逐页一个 Document，带 page_label）
    DOCX  DocxReader
    PPTX  PptxReader（逐页）
    XLSX  PandasExcelReader（逐 sheet）
    TXT   FlatReader

我们只补三件库不管的事（都是业务必需，不是重复造轮子）：
1. **页码 / 小节名归一化**：引用要能说「第几页 · 哪一节」，各 reader 的元数据键不一样，
   这里统一成 `page` / `section`。
2. **表格补充**：DOCX/PPTX 里的表格 readers 会漏，检索时"指标: 数值"必须成对出现，
   所以用 python-docx / python-pptx 再补一遍。
3. **扫描件兜底**：PDF 一页都抽不出字 → 抛 NeedOCR，由上层走 OCR（ocr.py）。
   静默入库一个空文档会让"为什么搜不到"变成玄学。
"""
from __future__ import annotations

import os
from pathlib import Path

from llama_index.core import Document

from src.infra.utils.LogUtil import log

ALLOWED_EXT: set[str] = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".txt", ".md", ".csv"}


class NeedOCR(Exception):
    """PDF 没有文字层（扫描件），需要走 OCR"""


_PAGE_KEYS: tuple[str, ...] = ("page_label", "page", "page_number", "slide", "slide_number", "Page")


def _clean(t: str) -> str:
    """清洗文本

    必须去掉 NUL(0x00)：PDF 里偶发 0 字节，PostgreSQL 的 text 字段直接拒收
    （报 "cannot contain NUL (0x00) bytes"），整篇文档会索引失败。
    """
    import re

    t = (t or "").replace("\u3000", " ").replace("\xa0", " ")
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _page_of(meta: dict, fallback: int | None) -> int | None:
    for k in _PAGE_KEYS:
        v = (meta or {}).get(k)
        if v is None:
            continue
        try:
            return int(str(v).strip())
        except (TypeError, ValueError):
            continue
    return fallback


def _first_line(text: str, limit: int = 60) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s:
            return s[:limit]
    return ""


def _docx_tables(path: str) -> list[Document]:
    """DOCX 表格补采：单元格按行拼成「列: 值」"""
    try:
        from docx import Document as _Docx

        d = _Docx(path)
    except Exception:
        return []
    out: list[Document] = []
    for ti, table in enumerate(d.tables):
        rows: list[str] = []
        for row in table.rows:
            cells = [_clean(c.text) for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            out.append(Document(text="\n".join(rows), metadata={"section": f"表格 {ti + 1}"}))
    return out


def _pptx_tables(path: str) -> dict[int, list[str]]:
    """PPTX 表格补采：返回 {页码: [行文本]}，由调用方并进对应页"""
    try:
        from pptx import Presentation

        prs = Presentation(path)
    except Exception:
        return {}
    out: dict[int, list[str]] = {}
    for i, slide in enumerate(prs.slides, 1):
        for shape in slide.shapes:
            if not getattr(shape, "has_table", False) or not shape.has_table:
                continue
            rows: list[str] = []
            for row in shape.table.rows:
                cells = [_clean(c.text) for c in row.cells]
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                out.setdefault(i, []).extend(rows)
    return out


def _docx_documents(path: str) -> tuple[list[Document], int]:
    """DOCX：按标题分节 + 表格补采

    不用 llama-index 的 DocxReader：它底层是 docx2txt，会丢掉标题层级和表格，
    而 python-docx 本来就是依赖项，直接用它更准。
    """
    from docx import Document as _Docx

    d = _Docx(path)
    docs: list[Document] = []
    buf: list[str] = []
    section = ""
    size = 0

    def flush() -> None:
        nonlocal buf, size
        if buf:
            docs.append(Document(text="\n".join(buf), metadata={"section": section}))
            buf = []
            size = 0

    for p in d.paragraphs:
        t = _clean(p.text)
        if not t:
            continue
        if (p.style.name or "").startswith("Heading"):
            flush()
            section = t[:60]
        buf.append(t)
        size += len(t)
        if size >= 800:
            flush()
    flush()
    docs.extend(_docx_tables(path))
    return docs, 0


def _load_raw(path: str) -> tuple[list[Document], int]:
    """各格式的原生读取，返回 (documents, 总页数)"""
    ext = os.path.splitext(path)[1].lower()
    p = Path(path)

    if ext == ".pdf":
        from llama_index.readers.file import PDFReader

        docs = PDFReader().load_data(file=p)
        total = sum(len(_clean(d.text)) for d in docs)
        if total < 80:
            raise NeedOCR(f"PDF 无文字层（{len(docs)} 页，抽出 {total} 字符）")
        return docs, len(docs)

    if ext == ".docx":
        return _docx_documents(path)

    if ext == ".pptx":
        from llama_index.readers.file import PptxReader

        docs = list(PptxReader().load_data(file=p))
        tables = _pptx_tables(path)
        if tables:
            merged: list[Document] = []
            for i, d in enumerate(docs, 1):
                extra = tables.get(i) or []
                text = d.text or ""
                if extra:
                    text = text + "\n" + "\n".join(extra)
                merged.append(Document(text=text, metadata=dict(d.metadata or {})))
            docs = merged
        return docs, len(docs)

    if ext in (".xlsx", ".xlsm"):
        from llama_index.readers.file import PandasExcelReader

        docs = list(PandasExcelReader().load_data(file=p))
        return docs, len(docs)

    if ext in (".txt", ".md", ".csv"):
        from llama_index.readers.file import FlatReader

        docs = list(FlatReader().load_data(file=p))
        return docs, 0

    raise ValueError(f"不支持的文件类型: {ext}（支持 {'/'.join(sorted(ALLOWED_EXT))}）")


def _stamp(docs: list[Document], *, doc_id: int, filename: str, scope: str, owner: str,
           paginate: bool = False) -> list[Document]:
    """统一元数据：检索过滤（scope/owner）与引用展示（filename/page/section）都靠它

    Document.text 是只读属性（LlamaIndex 0.14 起），所以这里重建对象而不是原地改。
    paginate=True 表示这种格式有真实页序（PDF/PPTX），缺页码时按顺序补；
    DOCX/XLSX/TXT 没有页的概念，宁可为空也不要编一个页码出来。
    """
    out: list[Document] = []
    for i, d in enumerate(docs):
        text = _clean(d.text)
        if not text:
            continue
        meta = dict(d.metadata or {})
        page = _page_of(meta, i + 1 if paginate else None)
        section = str(meta.get("section") or "").strip() or _first_line(text)
        # ref_doc_id 由 id_ 派生：整篇删除（重建索引）靠它
        out.append(Document(text=text, id_=f"{doc_id}:{i}", metadata={
            "doc_id": int(doc_id),
            "scope": scope,
            "owner": owner,
            "filename": filename,
            "page": page,
            "section": section[:200],
        }))
    return out


def load(path: str, *, doc_id: int, filename: str, scope: str, owner: str) -> tuple[list[Document], int]:
    """文件 → Document 列表（已带 page/section/scope/owner 元数据）"""
    docs, pages = _load_raw(path)
    ext = os.path.splitext(path)[1].lower()
    docs = _stamp(docs, doc_id=doc_id, filename=filename, scope=scope, owner=owner,
                  paginate=ext in (".pdf", ".pptx"))
    if not docs:
        raise ValueError("没有解析出任何文本（可能是空文件或格式异常）")
    log.info(f"知识库解析: doc={doc_id} file={filename} documents={len(docs)} pages={pages}")
    return docs, pages


def ocr_documents(blocks: list[dict], *, doc_id: int, filename: str, scope: str, owner: str) -> list[Document]:
    """OCR 结果（blocks: [{text,page,section}]）→ Document"""
    raw = [Document(text=b.get("text") or "",
                    metadata={"page": b.get("page"), "section": b.get("section") or ""})
           for b in blocks if _clean(b.get("text") or "")]
    if not raw:
        raise ValueError("OCR 结果为空")
    return _stamp(raw, doc_id=doc_id, filename=filename, scope=scope, owner=owner)
