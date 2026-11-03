"""沙箱 backend 的「静态路径读缓存」

为什么要有这个测试：缓存加错了会让**模型读到旧文件**（写 → 读同一路径拿到旧内容），
这是数据错误，比慢严重得多。所以这里把边界钉死：
  · /AGENT.md 的整读、/sandbox/skills 的列举 → 走缓存
  · 其它路径（模型自己产出的文件）→ 一律不缓存
  · 技能变更后 invalidate 能真的清掉
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestBackendCache(unittest.TestCase):
    def setUp(self):
        from src.agent.backends.openshell import invalidate_backend_cache

        invalidate_backend_cache()

    def test_allowlist_only_static_paths(self):
        from src.agent.backends import openshell as m

        self.assertIn("/AGENT.md", m._STATIC_READ_PATHS)
        for unsafe in ("/sandbox/output/a.xlsx", "/sandbox/upload/b.pdf", "/sandbox/notes.md"):
            self.assertNotIn(unsafe, m._STATIC_READ_PATHS)
        self.assertTrue(m._STATIC_LIST_PREFIXES, "技能目录要能缓存")

    def test_cache_hits_only_for_allowlisted_paths(self):
        from deepagents.backends.sandbox import BaseSandbox

        from src.agent.backends.openshell import OpenShellSandboxBackend, invalidate_backend_cache

        calls = {"read": 0, "ls": 0}
        orig_read, orig_ls = BaseSandbox.read, BaseSandbox.ls
        BaseSandbox.read = lambda self, p, offset=0, limit=2000: (
            calls.__setitem__("read", calls["read"] + 1) or f"content:{p}"
        )
        BaseSandbox.ls = lambda self, p: (
            calls.__setitem__("ls", calls["ls"] + 1) or f"ls:{p}"
        )
        try:
            b = OpenShellSandboxBackend("t_cache_user")

            # 静态文件：两次只读一次
            b.read("/AGENT.md")
            b.read("/AGENT.md")
            self.assertEqual(calls["read"], 1, "/AGENT.md 应命中缓存")

            # 非白名单：每次都真读（绝不能返回旧内容）
            b.read("/sandbox/output/x.xlsx")
            b.read("/sandbox/output/x.xlsx")
            self.assertEqual(calls["read"], 3, "模型产出的文件不能被缓存")

            # 技能目录列举：缓存
            b.ls("/sandbox/skills/")
            b.ls("/sandbox/skills/")
            self.assertEqual(calls["ls"], 1, "技能目录列举应命中缓存")

            # 其它目录：不缓存
            b.ls("/sandbox/output/")
            b.ls("/sandbox/output/")
            self.assertEqual(calls["ls"], 3, "普通目录不能被缓存")

            # 技能变更后必须失效
            invalidate_backend_cache("t_cache_user")
            b.read("/AGENT.md")
            self.assertEqual(calls["read"], 4, "invalidate 之后应重新读")
        finally:
            BaseSandbox.read, BaseSandbox.ls = orig_read, orig_ls

    def test_offset_read_bypasses_cache(self):
        """分页读（offset>0）不走缓存，避免"第二页从缓存取到第一页内容"这类事故"""
        from deepagents.backends.sandbox import BaseSandbox

        from src.agent.backends.openshell import OpenShellSandboxBackend, invalidate_backend_cache

        calls = {"n": 0}
        orig = BaseSandbox.read
        BaseSandbox.read = lambda self, p, offset=0, limit=2000: (
            calls.__setitem__("n", calls["n"] + 1) or f"{p}@{offset}"
        )
        try:
            b = OpenShellSandboxBackend("t_cache_user2")
            b.read("/AGENT.md", offset=100)
            b.read("/AGENT.md", offset=100)
            self.assertEqual(calls["n"], 2, "分页读不该被缓存")
        finally:
            BaseSandbox.read = orig
            invalidate_backend_cache()


if __name__ == "__main__":
    unittest.main()
