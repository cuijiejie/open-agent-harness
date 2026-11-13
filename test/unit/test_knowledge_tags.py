"""知识标签归一化：数组/字符串都要能存，不能 500 也不能存成 "['QA']"。"""
import unittest

from src.agent.memory.knowledge import _norm_tags


class TestNormTags(unittest.TestCase):
    def test_string_passthrough(self):
        self.assertEqual(_norm_tags("退货, 金额"), "退货, 金额")

    def test_list_joined(self):
        self.assertEqual(_norm_tags(["退货", "金额"]), "退货,金额")

    def test_list_dedup_and_strip(self):
        self.assertEqual(_norm_tags(["  退货 ", "退货", "", "金额"]), "退货,金额")

    def test_none_and_empty(self):
        self.assertEqual(_norm_tags(None), "")
        self.assertEqual(_norm_tags([]), "")

    def test_truncated_to_200(self):
        tags = [("长" * 19) + str(i) for i in range(20)]     # 20 条 × 20 字 = 400 字
        self.assertEqual(len(_norm_tags(tags)), 200)


if __name__ == "__main__":
    unittest.main()
