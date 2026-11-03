"""页面加载预热：节流逻辑

预热本身是"后台多做点活"，风险不在功能而在**刷接口会不会把沙箱打爆**，
所以这里把节流规则钉住：同一用户 120 秒内最多一次，空用户名直接不预热。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestPrewarmThrottle(unittest.TestCase):
    def setUp(self):
        from src.infra import Prewarm

        Prewarm.reset()

    def tearDown(self):
        from src.infra import Prewarm

        Prewarm.reset()

    def test_first_time_allowed_then_throttled(self):
        from src.infra.Prewarm import _WARM_TTL, _should_warm

        self.assertTrue(_should_warm("u1", now=1000.0), "首次应允许")
        self.assertFalse(_should_warm("u1", now=1000.0 + 1), "1 秒内应被节流")
        self.assertFalse(_should_warm("u1", now=1000.0 + _WARM_TTL - 1), "TTL 内应被节流")
        self.assertTrue(_should_warm("u1", now=1000.0 + _WARM_TTL + 1), "超过 TTL 应再次允许")

    def test_per_user_independent(self):
        from src.infra.Prewarm import _should_warm

        self.assertTrue(_should_warm("u1", now=2000.0))
        self.assertTrue(_should_warm("u2", now=2000.0), "不同用户互不影响")

    def test_empty_username_never_warms(self):
        from src.infra.Prewarm import _should_warm

        self.assertFalse(_should_warm("", now=3000.0))

    def test_warm_user_returns_true_only_when_started(self):
        """真的会起线程：第一次 True、紧接着 False（不影响功能，只是多跑点读操作）"""
        from src.infra.Prewarm import warm_user

        # 用不存在的用户：load_skill_tools 会因沙箱不可用直接返回空，不会真扫
        self.assertTrue(warm_user("t_prewarm_probe"))
        self.assertFalse(warm_user("t_prewarm_probe"))


if __name__ == "__main__":
    unittest.main()
