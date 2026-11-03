"""平台技能：命名/版本这类纯逻辑

`_base_of` / `_ver_of` 决定「同名再发布 → 新增版本」到底对不对，
写错了表现是"发布两次变成两个不相关技能"或"覆盖掉旧版本"，很难现场发现。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestBaseOf(unittest.TestCase):
    def test_plain_name(self):
        from src.controller.PlatformSkillController import _base_of

        self.assertEqual(_base_of("office-file-toolkit"), "office-file-toolkit")

    def test_versioned_name(self):
        from src.controller.PlatformSkillController import _base_of

        self.assertEqual(_base_of("office-file-toolkit-v2"), "office-file-toolkit")
        self.assertEqual(_base_of("office-file-toolkit-v17"), "office-file-toolkit")

    def test_name_ending_with_v_is_not_version(self):
        """尾部的 -v 但没有数字，不能当版本号（否则名字会被削掉）"""
        from src.controller.PlatformSkillController import _base_of

        self.assertEqual(_base_of("skill-v"), "skill-v")

    def test_empty(self):
        from src.controller.PlatformSkillController import _base_of

        self.assertEqual(_base_of(""), "")


class TestVerOf(unittest.TestCase):
    def test_unversioned_is_1(self):
        from src.controller.PlatformSkillController import _ver_of

        self.assertEqual(_ver_of("office-file-toolkit"), 1)

    def test_versioned(self):
        from src.controller.PlatformSkillController import _ver_of

        self.assertEqual(_ver_of("office-file-toolkit-v2"), 2)
        self.assertEqual(_ver_of("office-file-toolkit-v10"), 10)

    def test_v_without_digit(self):
        from src.controller.PlatformSkillController import _ver_of

        self.assertEqual(_ver_of("skill-v"), 1)


class TestSkillNameSafety(unittest.TestCase):
    """平台技能的路径都靠 _safe_name 挡穿越"""

    def test_rejects_traversal(self):
        from src.controller.SkillController import _safe_name

        for bad in ("../etc", "a/b", "..", "", "a\\b"):
            with self.assertRaises(ValueError, msg=f"{bad!r} 应该被拒"):
                _safe_name(bad)

    def test_accepts_normal(self):
        from src.controller.SkillController import _safe_name

        for good in ("office-file-toolkit", "t-plat-abc123", "skill_v2"):
            self.assertEqual(_safe_name(good), good)


if __name__ == "__main__":
    unittest.main()
