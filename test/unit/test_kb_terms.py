"""关键词抽取：通用片段（纯数字、停用 bigram）不该进入召回条件。"""
import unittest

from src.infra.kb.retrieve import terms


class TestTerms(unittest.TestCase):
    def test_keeps_full_code(self):
        self.assertIn("qakb9900", terms("QAKB9900"))

    def test_drops_short_digits(self):
        ts = terms("99 900 2024")
        self.assertNotIn("99", ts)
        self.assertNotIn("900", ts)
        self.assertIn("2024", ts)          # 4 位年份有检索价值，保留

    def test_cjk_bigrams(self):
        ts = terms("退货政策")
        self.assertEqual(ts, ["退货", "货政", "政策"])

    def test_stop_chars_removed(self):
        ts = terms("的了")
        self.assertEqual(ts, [])

    def test_empty_query(self):
        self.assertEqual(terms(""), [])


if __name__ == "__main__":
    unittest.main()
