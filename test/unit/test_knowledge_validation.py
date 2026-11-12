"""业务知识（pa_knowledge）的入参校验：不连库也能测

update() / add() 的校验都在建立数据库连接之前返回，所以这些用例不需要 MySQL。
"""
import unittest

try:
    from src.agent.memory import knowledge as K

    _HAS = True
except Exception:
    _HAS = False


@unittest.skipUnless(_HAS, "缺少依赖")
class TestKnowledgeValidation(unittest.TestCase):
    def test_update_requires_some_field(self):
        self.assertFalse(K.update(1)["ok"])

    def test_update_rejects_empty_content(self):
        res = K.update(1, content="   ")
        self.assertFalse(res["ok"])
        self.assertIn("不能为空", res["error"])

    def test_update_rejects_too_long_content(self):
        res = K.update(1, content="字" * (K.MAX_CONTENT + 1))
        self.assertFalse(res["ok"])
        self.assertIn("太长", res["error"])

    def test_add_rejects_empty_and_long(self):
        self.assertFalse(K.add("")["ok"])
        self.assertFalse(K.add("字" * (K.MAX_CONTENT + 1))["ok"])

    def test_limits(self):
        self.assertEqual(K.MAX_CONTENT, 500)
        self.assertEqual(K.MAX_ITEMS_PER_USER, 50)
        self.assertEqual(K.RECALL_LIMIT, 3)
        self.assertEqual(K.HINT_BUDGET, 800)


if __name__ == "__main__":
    unittest.main()
