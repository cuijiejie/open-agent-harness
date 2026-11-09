"""文档解析：把 PDF / DOCX / XLSX / PPTX / TXT 变成长文本块（带页码/来源）

设计要点
--------
· **页码要保住** —— 引用要能定位"第几页"，所以 PDF 按页解析，DOCX/PPTX 记段落序号，
  XLSX 记工作表名（当作 section）。
· **表格单独处理** —— 表格直接按行拼成「列名: 值」，否则切分会把指标和数字拆散。
· **扫描件识别** —— PDF 一页都抽不出文字就抛 NeedOCR，让上层标记为 failed 并提示用户，
  而不是静默入库一个空文档（那会让"为什么搜不到"变成玄学）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


class NeedOCR(Exception):
    """PDF 没有文字层（扫描件），需要走 OCR"""


@dataclass
class Block:
    text: str
    page: int | None = None
    section: str = ""


@dataclass
class Parsed:
    blocks: list[Block] = field(default_factory=list)
    pages: int = 0


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(t: str) -> str:
    """清洗文本

    必须去掉 NUL(0x00)：PDF 里偶发 0 字节，PostgreSQL 的 text 字段直接拒收
    （报 "cannot contain NUL (0x00) bytes"），整篇文档会索引失败。
    其它控制字符一并清掉，只保留换行/制表。
    """
    t = t.replace("\u3000", " ").replace("\xa0", " ")
    t = _CTRL.sub("", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _parse_pdf(path: str) -> Parsed:
    import pymupdf

    doc = pymupdf.open(path)
    out = Parsed(pages=len(doc))
    total_chars = 0
    for i, page in enumerate(doc):
        txt = _clean(page.get_text())
        total_chars += len(txt)
        if txt:
            # 取首行当小节名（统计公报的"一、综合"这类），便于引用展示
            head = txt.split("\n")[0][:60]
            out.blocks.append(Block(text=txt, page=i + 1, section=head))
    if total_chars < 80:
        raise NeedOCR(f"PDF 无文字层（{len(doc)} 页，抽出 {total_chars} 字符）")
    return out


def _parse_docx(path: str) -> Parsed:
    from docx import Document

    d = Document(path)
    out = Parsed()
    buf: list[str] = []
    section = ""
    for p in d.paragraphs:
        t = _clean(p.text)
        if not t:
            continue
        if p.style.name.startswith("Heading"):
            section = t[:60]
        buf.append(t)
        if len("\n".join(buf)) >= 800:
            out.blocks.append(Block(text="\n".join(buf), section=section))
            buf = []
    for ti, table in enumerate(d.tables):
        rows = []
        for row in table.rows:
            cells = [_clean(c.text) for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            out.blocks.append(Block(text="\n".join(rows), section=f"表格 {ti + 1}"))
    if buf:
        out.blocks.append(Block(text="\n".join(buf), section=section))
    out.pages = 0
    return out


def _parse_xlsx(path: str) -> Parsed:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    out = Parsed(pages=len(wb.sheetnames))
    for si, name in enumerate(wb.sheetnames):
        ws = wb[name]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        lines: list[str] = [f"工作表：{name}"]
        for r in rows[1:]:
            vals = [str(c).strip() if c is not None else "" for c in r]
            pairs = [f"{header[i]}: {vals[i]}" for i in range(min(len(header), len(vals)))
                     if header[i] and vals[i]]
            if pairs:
                lines.append("；".join(pairs))
        text = _clean("\n".join(lines))
        if text:
            out.blocks.append(Block(text=text, page=si + 1, section=name))
    wb.close()
    return out


def _parse_pptx(path: str) -> Parsed:
    from pptx import Presentation

    prs = Presentation(path)
    out = Parsed(pages=len(prs.slides))
    for i, slide in enumerate(prs.slides):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = _clean(shape.text_frame.text)
                if t:
                    parts.append(t)
            if getattr(shape, "has_table", False) and shape.has_table:
                for row in shape.table.rows:
                    cells = [_clean(c.text) for c in row.cells]
                    if any(cells):
                        parts.append(" | ".join(cells))
        if parts:
            out.blocks.append(Block(text="\n".join(parts), page=i + 1,
                                    section=f"幻灯片 {i + 1}"))
    return out


def _parse_text(path: str) -> Parsed:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return Parsed(blocks=[Block(text=_clean(f.read()))])


_PARSERS = {
    ".pdf": _parse_pdf,
    ".docx": _parse_docx,
    ".doc": _parse_docx,        # 老 .doc 其实解析不了，明确报错更诚实
    ".xlsx": _parse_xlsx,
    ".xlsm": _parse_xlsx,
    ".pptx": _parse_pptx,
    ".txt": _parse_text,
    ".md": _parse_text,
    ".csv": _parse_text,
}


def parse_document(path: str) -> Parsed:
    ext = os.path.splitext(path)[1].lower()
    fn = _PARSERS.get(ext)
    if not fn:
        raise ValueError(f"不支持的文件类型: {ext}（支持 pdf/docx/xlsx/pptx/txt/md/csv）")
    parsed = fn(path)
    if not parsed.blocks:
        raise ValueError("没有解析出任何文本（可能是空文件或格式异常）")
    return parsed
