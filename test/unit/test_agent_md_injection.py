"""AGENT.md 注入：宿主读文件 + 拼到 system prompt

这是一次**真 bug 的回归测试**：
原来用 deepagents 的 `memory=["/AGENT.md"]`，它按沙箱路径读，
而沙箱里没有这个文件 → 每次新会话白跑一次 exec（1~3.7 秒），
仓库里 19KB 的 AGENT.md 从来没进过模型。

这里钉住三件事：
  · 能读到仓库 AGENT.md（内容不为空、是那份文件）
  · 注入后 system prompt 里真的有 AGENT.md 的内容
  · 重复注入不会叠加（wrap_model_call 每轮都调用）
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeRequest:
    """模拟 langchain ModelRequest 的最小结构"""

    def __init__(self, system_prompt: str = "你是助手") -> None:
        self.system_prompt = system_prompt
        self.messages: list = []


class TestLoadAgentMd(unittest.TestCase):
    def test_reads_repo_agent_md(self):
        from src.agent.middlewares.HostMemoryMiddleware import load_agent_md

        text = load_agent_md()
        self.assertGreater(len(text), 1000, f"AGENT.md 内容过短（{len(text)} 字符），可能没读到")
        self.assertIn("AGENT", text.upper()[:2000] or text.upper())

    def test_missing_file_returns_empty(self):
        from src.agent.middlewares.HostMemoryMiddleware import load_agent_md

        self.assertEqual(load_agent_md("/nonexistent/AGENT.md"), "")

    def test_truncates_huge_file(self):
        """超大文件要截断，不能把系统提示撑爆"""
        import tempfile

        from src.agent.middlewares import HostMemoryMiddleware as m

        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
            fh.write("x" * (m._MAX_CHARS + 500))
            p = fh.name
        try:
            text = m.load_agent_md(p)
        finally:
            Path(p).unlink(missing_ok=True)
        self.assertEqual(len(text), m._MAX_CHARS)


class TestInjection(unittest.TestCase):
    def test_injects_into_system_prompt(self):
        from src.agent.middlewares.HostMemoryMiddleware import HostMemoryMiddleware

        mw = HostMemoryMiddleware()
        self.assertTrue(mw._text, "AGENT.md 应该读到了内容")
        req = FakeRequest("你是助手")
        called = {"n": 0}

        def handler(r):
            called["n"] += 1
            return "ok"

        mw.wrap_model_call(req, handler)
        self.assertEqual(called["n"], 1, "必须继续调用下游 handler")
        self.assertIn("你是助手", req.system_prompt)
        self.assertIn(mw._text[:50], req.system_prompt, "system prompt 里应包含 AGENT.md 内容")

    def test_idempotent(self):
        """每轮都会调 wrap_model_call，不能越拼越长"""
        from src.agent.middlewares.HostMemoryMiddleware import HostMemoryMiddleware

        mw = HostMemoryMiddleware()
        req = FakeRequest()
        handler = lambda r: "ok"  # noqa: E731
        mw.wrap_model_call(req, handler)
        first = len(req.system_prompt)
        for _ in range(3):
            mw.wrap_model_call(req, handler)
        self.assertEqual(len(req.system_prompt), first, "重复注入导致提示词膨胀")

    def test_empty_text_is_noop(self):
        from src.agent.middlewares.HostMemoryMiddleware import HostMemoryMiddleware

        mw = HostMemoryMiddleware("/nonexistent/AGENT.md")
        req = FakeRequest("原始提示")
        mw.wrap_model_call(req, lambda r: "ok")
        self.assertEqual(req.system_prompt, "原始提示")


if __name__ == "__main__":
    unittest.main()
