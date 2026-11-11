"""LlamaIndex 全局配置（进程内只初始化一次）

为什么集中在这里
----------------
LlamaIndex 的切分器 / 嵌入模型都挂在全局 `Settings` 上，
散落在各模块里各设一份，就会出现「入库用一个切分器、检索用另一个」的漂移。
所以全仓库只有这一个地方设置 Settings，其它模块 import 后直接用。

两个刻意的选择
--------------
1. **不做 LLM 侧合成**：`Settings.llm = None`。检索结果交给智能体组织回答，
   平台不需要 QueryEngine 再吐一遍自然语言（那会丢掉工具调用能力，还多一次计费）。
2. **按字符数切分**：LlamaIndex 默认用 tiktoken（BPE token）。中文 700 token
   约等于 450 字，且 tiktoken 首次运行要下载编码表（内网机器会卡住）。
   这里把 tokenizer 换成「按字符」，chunk_size 就回到直观的「字数」。
"""
from __future__ import annotations

from llama_index.core import Settings
from llama_index.core.node_parser import SentenceSplitter

from src.infra.kb.embedding import LocalEmbedding

_ready: bool = False


def char_tokenizer(text: str) -> list[str]:
    """一个汉字/字符 = 一个 token（只用于计数，不涉及模型）"""
    return list(text or "")


def configure(force: bool = False) -> None:
    """初始化 Settings（幂等）。force=True 用于配置热更后重建。"""
    global _ready
    if _ready and not force:
        return

    from config.Configs import cfg

    Settings.embed_model = LocalEmbedding()
    Settings.node_parser = SentenceSplitter(
        chunk_size=int(getattr(cfg, "kbChunkSize", 700)),
        chunk_overlap=int(getattr(cfg, "kbChunkOverlap", 100)),
        paragraph_separator="\n\n",
        # 中文句末标点优先断句，避免把「指标 16737.30 亿元」从中间切开
        secondary_chunking_regex="[^。！？；!?;\n]+[。！？；!?;]?",
        chunking_tokenizer_fn=char_tokenizer,
        # True：把 Document.metadata（doc_id/scope/owner/filename/page/section）带到 node 上，
        # 检索过滤与引用展示都依赖它；它不会往正文里注入额外文字
        include_metadata=True,
        include_prev_next_rel=False,
    )
    Settings.llm = None
    _ready = True


def splitter() -> SentenceSplitter:
    configure()
    parser = Settings.node_parser
    assert isinstance(parser, SentenceSplitter)
    return parser
