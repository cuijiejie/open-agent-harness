"""护栏单测：这些判断一旦错了就是"线上事故"，不是样式问题

跑法：python -m unittest discover -s test/unit -v
需要装依赖（langchain/flask…）；本机没装时 pre-commit 会跳过。

覆盖：
  · 沙箱命名（gateway 约束：小写/数字/连字符、≤19；有损映射必须带哈希）
  · 导航 URL 白名单（同源 XSS 红线）
  · 产出文件块（链接/跳过数量/大小显示）
  · resume 前的"有没有待确认中断"判断
  · 审计脱敏、轨迹截断
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestSandboxName(unittest.TestCase):
    """沙箱名必须合法且可区分（曾因下划线/中文/超长导致整类账号沙箱不可用）"""

    def setUp(self):
        from src.agent.backends.sandbox import sandbox_manager as sm

        self.sm = sm

    def _assert_legal(self, name: str):
        self.assertTrue(name.startswith("agent-"), name)
        self.assertLessEqual(len(name), 19, name)
        body = name[len("agent-"):]
        self.assertEqual(body, body.lower(), name)
        self.assertTrue(all(c.isalnum() or c == "-" for c in body), name)
        self.assertFalse(body.startswith("-") or body.endswith("-"), name)

    def test_normal_account(self):
        # 纯字母数字账号：名字原样保留（不补哈希，保住已有沙箱）
        self.assertEqual(self.sm.sandbox_name("alice"), "agent-alice")
        # 带 `.` 的账号属于有损映射 → 必须带 6 位哈希（alice.wang → agent-alice-xxxxxx）
        n = self.sm.sandbox_name("alice.wang")
        self._assert_legal(n)
        self.assertRegex(n, r"^agent-alice-[0-9a-f]{6}$")

    def test_lowercase_only_account(self):
        self.assertEqual(self.sm.sandbox_name("alice"), "agent-alice")

    def test_lossy_mapping_gets_hash(self):
        n = self.sm.sandbox_name("alice.wang")
        n2 = self.sm.sandbox_name("alice_wang")
        self._assert_legal(n2)
        self.assertNotEqual(n, n2, "两个不同账号不能撞进同一个沙箱")

    def test_cjk_account(self):
        n = self.sm.sandbox_name("李雷")
        self._assert_legal(n)          # 中文不能出现在名字里

    def test_long_account(self):
        n = self.sm.sandbox_name("averyveryverylongaccountname")
        self._assert_legal(n)

    def test_uppercase_account(self):
        n = self.sm.sandbox_name("Cui.Jie")
        self._assert_legal(n)

    def test_stable(self):
        """同一个账号每次算出来必须一样，否则会反复建新沙箱"""
        self.assertEqual(self.sm.sandbox_name("Cui.Jie"), self.sm.sandbox_name("Cui.Jie"))

    def test_empty(self):
        self._assert_legal(self.sm.sandbox_name(""))


class TestNavUrlWhitelist(unittest.TestCase):
    """后端只放行 http(s) 和站内绝对路径；前端还会再挡一次（双保险）"""

    def test_allows(self):
        from src.chat.stream import _is_safe_nav_url

        for u in ("https://x/y", "HTTP://X.Y", "/open-agent-harness/a", "  /a  "):
            self.assertTrue(_is_safe_nav_url(u), u)

    def test_rejects(self):
        from src.chat.stream import _is_safe_nav_url

        for u in ("javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x",
                  "vbscript:m", "file:///etc/passwd", "//evil.com", "ftp://x", "",
                  "relative/path", None):
            self.assertFalse(_is_safe_nav_url(u), repr(u))


class TestArtifactBlock(unittest.TestCase):
    """产出文件块：链接要对、跳过的数量要说明、大小要人话"""

    def _block(self, arts, skipped=0):
        from src.chat.stream import _artifact_block

        return _artifact_block("s1", arts, skipped=skipped)

    def test_links_and_names(self):
        from urllib.parse import quote

        out = self._block([
            {"name": "报告 2026.xlsx", "size": 2048},
            {"name": "chart.png", "size": 900},
        ])
        # 文件名要 URL 编码（空格/中文不能直接拼进路径）
        self.assertIn(quote("报告 2026.xlsx", safe=""), out)
        self.assertIn("/open-agent-harness/api/artifacts/s1/", out)
        self.assertIn("chart.png", out)

    def test_size_text(self):
        out = self._block([
            {"name": "tiny.txt", "size": 500},
            {"name": "mid.txt", "size": 300 * 1024},
            {"name": "big.bin", "size": 5 * 1024 * 1024},
        ])
        self.assertIn("500 B", out)
        self.assertIn("300.0 KB", out)
        self.assertIn("5.0 MB", out)

    def test_inline_vs_download_only(self):
        out = self._block([{"name": "a.html", "size": 10}, {"name": "b.zip", "size": 10}])
        html_line = [ln for ln in out.splitlines() if "a.html" in ln][0]
        zip_line = [ln for ln in out.splitlines() if "b.zip" in ln][0]
        # 能直接在浏览器打开的：给「打开 + 下载」两个入口
        self.assertEqual(html_line.count("/api/artifacts/"), 2, html_line)
        self.assertIn("?download=1", html_line)
        # 压缩包这类：只给下载入口
        self.assertEqual(zip_line.count("/api/artifacts/"), 1, zip_line)
        self.assertIn("?download=1", zip_line)

    def test_skipped_notice(self):
        out = self._block([{"name": "x.txt", "size": 1}], skipped=3)
        self.assertIn("另有 **3** 个文件本轮未回传", out)

    def test_empty_name_ignored(self):
        out = self._block([{"name": "", "size": 1}, {"name": "ok.txt", "size": 1}])
        # 空名不生成链接：只剩 ok.txt 那一行的 2 个链接（打开 + 下载）
        self.assertEqual(out.count("/api/artifacts/s1/"), 2, out)


class TestHasPendingInterrupt(unittest.TestCase):
    """刷新后重复点选项：必须能判断"线程是不是真的停在待确认状态"""

    class _Snap:
        def __init__(self, next_):
            self.next = next_

    class _Agent:
        """假 agent：_has_pending_interrupt 异步化后走 `aget_state`"""

        def __init__(self, next_=(), boom=False):
            self._next = next_
            self._boom = boom

        async def aget_state(self, _cfg):
            if self._boom:
                raise RuntimeError("checkpointer down")
            return TestHasPendingInterrupt._Snap(self._next)

    def test_pending(self):
        import asyncio

        from src.chat.stream import _has_pending_interrupt

        self.assertTrue(asyncio.run(_has_pending_interrupt(self._Agent(("model",)), {})))

    def test_finished(self):
        import asyncio

        from src.chat.stream import _has_pending_interrupt

        self.assertFalse(asyncio.run(_has_pending_interrupt(self._Agent(()), {})))

    def test_query_failure_allows(self):
        """查询失败要放行：宁可多跑一次，也别把正常恢复挡死"""
        import asyncio

        from src.chat.stream import _has_pending_interrupt

        self.assertTrue(asyncio.run(_has_pending_interrupt(self._Agent(boom=True), {})))


class TestAuditMask(unittest.TestCase):
    """审计表长期保留，口令/token 不能明文落盘（含嵌套和字符串内）"""

    def test_nested_dict_masked(self):
        from src.infra.AuditService import _mask

        out = _mask({"env": {"DB_PASSWORD": "s3cret", "TOKEN": "abc123"}, "ok": 1})
        self.assertNotIn("s3cret", str(out))
        self.assertNotIn("abc123", str(out))
        self.assertEqual(out["ok"], 1)

    def test_text_masked(self):
        from src.infra.AuditService import _mask_text

        out = _mask_text("mysql -h 1.2.3.4 -u root password=pa55w0rd token=zzz")
        self.assertNotIn("pa55w0rd", out)
        self.assertNotIn("zzz", out)

    def test_deep_structure_no_crash(self):
        from src.infra.AuditService import _mask

        deep = {"a": {f"k{i}": {"b": {"c": {"d": {"e": "x"}}}} for i in range(3)}}
        self.assertIsInstance(_mask(deep), dict)


class TestClip(unittest.TestCase):
    """轨迹文本截断：保留尾部（错误信息常在最后）、且不超长"""

    def test_short_untouched(self):
        from src.infra.TraceService import _clip

        self.assertEqual(_clip("abc", 10), "abc")

    def test_long_keeps_tail(self):
        from src.infra.TraceService import _clip

        s = "A" * 900 + "TAIL_MARKER"
        out = _clip(s, 200)
        self.assertIn("TAIL_MARKER", out)
        self.assertLess(len(out), 400)
        self.assertIn("省略", out)

    def test_non_str(self):
        from src.infra.TraceService import _clip

        self.assertIn("123", _clip(12345, 100))


if __name__ == "__main__":
    unittest.main()
