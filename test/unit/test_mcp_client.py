"""MCP 客户端（langchain-mcp-adapters 版）纯函数测试

覆盖：连接构建（只支持 streamable_http）、URL/传输校验、请求头解析、返回值规整。
不连网、不连库，跑得快。
"""
import unittest

try:
    from src.agent.mcp.client import (
        TRANSPORTS,
        _to_text,
        build_connection,
        normalize_headers,
        validate_remote,
    )

    _HAS_DEPS = True
except Exception:      # 缺 langchain / adapter 时跳过
    _HAS_DEPS = False


@unittest.skipUnless(_HAS_DEPS, "缺少 langchain-mcp-adapters 依赖")
class TestBuildConnection(unittest.TestCase):
    def test_remote_needs_http_url(self):
        self.assertIsNone(build_connection({"name": "a", "transport": "streamable_http",
                                            "url": "ftp://x"}))
        self.assertIsNone(build_connection({"name": "a", "transport": "streamable_http"}))
        conn = build_connection({"name": "a", "transport": "streamable_http",
                                 "url": "https://mcp.example.com/mcp",
                                 "headers": {"Authorization": "Bearer t"}}, timeout=15)
        self.assertEqual(conn["url"], "https://mcp.example.com/mcp")
        self.assertEqual(conn["headers"], {"Authorization": "Bearer t"})
        self.assertEqual(conn["timeout"], 15.0)

    def test_only_streamable_http(self):
        """stdio / sse 一律拒绝：本项目只做远程传输"""
        self.assertIsNone(build_connection({"name": "a", "transport": "sse", "url": "https://x/sse"}))
        self.assertIsNone(build_connection({"name": "a", "transport": "stdio", "command": "python3", "url": "https://x"}))

    def test_unknown_transport_rejected(self):
        self.assertIsNone(build_connection({"name": "a", "transport": "grpc", "url": "https://x"}))

    def test_legacy_row_without_transport(self):
        """老数据没写 transport：有 url 就按 streamable_http 处理"""
        conn = build_connection({"name": "a", "url": "https://x/mcp"})
        self.assertEqual(conn["transport"], "streamable_http")

    def test_missing_name(self):
        self.assertIsNone(build_connection({"command": "node"}))


@unittest.skipUnless(_HAS_DEPS, "缺少 langchain-mcp-adapters 依赖")
class TestHelpers(unittest.TestCase):
    def test_normalize_headers_dict_and_text(self):
        self.assertEqual(normalize_headers({"A": "1", " ": "x"}), {"A": "1"})
        self.assertEqual(normalize_headers("Authorization: Bearer t\nX-Trace: 1\n坏行"),
                         {"Authorization": "Bearer t", "X-Trace": "1"})
        self.assertEqual(normalize_headers(None), {})

    def test_validate_remote(self):
        self.assertEqual(validate_remote("ok_name-1", "streamable_http", "https://x/mcp"), "")
        self.assertIn("只能用小写", validate_remote("Bad Name", "streamable_http", "https://x"))
        self.assertIn("streamable_http", validate_remote("ok", "stdio", "https://x"))
        self.assertIn("http", validate_remote("ok", "streamable_http", "ws://x"))

    def test_transports_single_source(self):
        self.assertEqual(set(TRANSPORTS), {"streamable_http"})

    def test_to_text_shapes(self):
        self.assertEqual(_to_text("hi"), "hi")
        self.assertEqual(_to_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]), "a\nb")
        self.assertEqual(_to_text(None), "（工具执行成功，无文本输出）")
        self.assertEqual(_to_text(("ok", {"artifact": 1})), "ok")
        self.assertIn("已截断", _to_text("x" * 9000))


if __name__ == "__main__":
    unittest.main()
