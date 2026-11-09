"""扫描件 OCR：把没有文字层的 PDF 逐页渲染成图，交给视觉模型识别

为什么走"视觉模型"而不是本地方案（tesseract/paddleocr）
---------------------------------------------------------
· 本机没有 GPU，paddleocr 在 8 核 CPU 上一页要几秒到十几秒，且要多装 1GB+ 依赖
· 公司有内部的 OpenAI 协议视觉网关，识别中文表格的效果明显好于通用 tesseract
· 数据不出内网（网关是内网域名），符合"文档不许离开内网"的约束

设计要点
--------
1. **只对"无文字层"的页做 OCR**：有文字层的页直接用 PDF 里的文字，省时间也避免识别误差
2. **结果落盘缓存**（data/kb/ocr/<doc_id>/p<页码>.txt）：重索引/重试不重复调用模型
3. **页数上限**：默认只 OCR 前 30 页，防止一本 300 页的扫描书把网关打满
4. **失败可降级**：模型不可用时不阻断整篇，保留已成功的页；一页都没成功才报错
"""
from __future__ import annotations

import base64
import os
import re

from src.infra.utils.LogUtil import log

_PROMPT: str = (
    "请把这张图片里的文字完整转成纯文本，用于后续检索。要求：\n"
    "1) 保持原有阅读顺序（含标题、正文、表格）；\n"
    "2) 表格按行输出，单元格之间用「 | 」分隔；\n"
    "3) 不要总结、不要翻译、不要添加解释，只输出识别到的文字；\n"
    "4) 无法辨认的字符用「□」代替。"
)


def _cfg(name: str, default):
    from config.Configs import cfg

    return getattr(cfg, name, default)


def enabled() -> bool:
    return bool(_cfg("kbOcrEnabled", False)) and bool(_cfg("kbOcrBaseUrl", ""))


def _cache_dir(doc_id: int) -> str:
    root = str(_cfg("kbDataDir", "") or os.path.join(os.getcwd(), "data", "kb"))
    d = os.path.join(root, "ocr", str(doc_id))
    os.makedirs(d, exist_ok=True)
    return d


def _render_png(page, dpi: int) -> bytes:
    pix = page.get_pixmap(dpi=dpi)
    return pix.tobytes("png")


def _call_vision(png: bytes, page_no: int) -> str:
    """调用 OpenAI 协议的视觉接口，返回识别文本"""
    import requests

    base = str(_cfg("kbOcrBaseUrl", "")).rstrip("/")
    key = str(_cfg("kbOcrApiKey", "") or "")
    model = str(_cfg("kbOcrModel", ""))
    timeout = int(_cfg("kbOcrTimeoutS", 120))
    b64 = base64.b64encode(png).decode("ascii")
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }],
    }
    r = requests.post(f"{base}/chat/completions", json=payload, timeout=timeout,
                      headers={"Authorization": f"Bearer {key}"} if key else {})
    r.raise_for_status()
    data = r.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    if not text.strip():
        raise RuntimeError(f"第 {page_no} 页识别结果为空")
    return text


def ocr_pdf(path: str, doc_id: int) -> tuple[list[dict], int]:
    """对 PDF 做 OCR，返回 (blocks, 总页数)

    blocks: [{"text", "page", "section"}]，与 parse.Block 同构
    """
    import pymupdf

    dpi = int(_cfg("kbOcrDpi", 150))
    max_pages = int(_cfg("kbOcrMaxPages", 30))
    doc = pymupdf.open(path)
    total = len(doc)
    cache = _cache_dir(doc_id)
    blocks: list[dict] = []
    ok_pages = 0

    for i, page in enumerate(doc):
        if i >= max_pages:
            log.warning(f"OCR 达到页数上限 {max_pages}，后续页跳过: doc={doc_id} 共{total}页")
            break
        page_no = i + 1
        cache_file = os.path.join(cache, f"p{page_no}.txt")
        text = ""
        if os.path.isfile(cache_file):
            text = open(cache_file, encoding="utf-8").read()
        else:
            # 先看这一页本身有没有文字层；有就直接用，别浪费 OCR
            native = (page.get_text() or "").strip()
            if len(native) >= 40:
                text = native
            else:
                try:
                    text = _call_vision(_render_png(page, dpi), page_no)
                except Exception as e:
                    log.warning(f"OCR 失败（第 {page_no} 页）: doc={doc_id} {e}")
                    continue
                with open(cache_file, "w", encoding="utf-8") as f:
                    f.write(text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            first = text.split("\n")[0][:60]
            blocks.append({"text": text, "page": page_no, "section": f"OCR · {first}"})
            ok_pages += 1

    log.warning(f"OCR 完成: doc={doc_id} 成功 {ok_pages}/{min(total, max_pages)} 页（总{total}页）")
    if ok_pages == 0:
        raise RuntimeError("OCR 一页都没识别成功（检查视觉网关配置）")
    return blocks, total
