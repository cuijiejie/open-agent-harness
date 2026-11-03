"""技能自带工具：一次扫描解析 + 空集提前返回

背景（实测）：新会话第一句要等 22 秒，其中 `load_skill_tools` 占 4.3 秒 ——
它原来先 `ls`、再对**每个技能**各 `cat tools.json`，N+1 次沙箱往返（每次 1~3.5 秒）。
现在改成一条命令批量输出 + 本地解析，并且「按需披露且本会话没读过技能」时直接返回、
连那一次往返都不做。

这两个改动都属于"看着没问题、实测差 4 秒"的类型，必须有测试兜住。
"""
from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 每次跑用全新的用户名：技能工具扫描结果现在会写 Redis（cache:skilltools:<user>），
# 用 t_e2e_a 这种真实账号会被线上缓存污染，用例结果就取决于"上次谁扫过"
UNIT_USER = f"t_unit_{uuid.uuid4().hex[:8]}"


class FakeSandbox:
    """只实现 load_skill_tools/_scan_skill_tools 用到的那点接口"""

    enabled = True

    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.calls: list[str] = []

    def exec(self, username: str, cmd: str, timeout: int = 0):
        self.calls.append(cmd)

        class R:
            returncode = 0
            stderr = ""
            error = ""

        R.stdout = self.stdout
        return R()


def _payload(skills: dict[str, str]) -> str:
    """按 _scan_skill_tools 的协议拼沙箱输出"""
    out = []
    for name, body in skills.items():
        out.append(f"@@SKILL\t{name}")
        out.append(body)
        out.append("@@END@@")
    return "\n".join(out) + "\n"


class TestScanSkillTools(unittest.TestCase):
    def _scan(self, sandbox):
        from src.infra.SkillToolsService import _scan_skill_tools

        # 每次用**新用户名**：扫描结果会写 Redis，同一个名字会让第一个用例的
        # 空结果命中到后面的用例（实测就是这么挂的）
        return _scan_skill_tools(sandbox, f"t_unit_{uuid.uuid4().hex[:8]}")

    def test_parses_multiple_skills_in_one_exec(self):
        sb = FakeSandbox(_payload({
            "office": '{"tools": [{"name": "xlsx2pdf", "description": "转 pdf"}]}',
            "rate": '{"tools": [{"name": "rate_lookup"}]}',
        }))
        specs = self._scan(sb)
        self.assertEqual(sorted(specs), ["office", "rate"], f"解析结果: {specs}")
        self.assertEqual(len(specs["office"]), 1)
        self.assertEqual(len(sb.calls), 1, "整批只能有一次沙箱往返")

    def test_skips_bad_json_and_missing_tools(self):
        sb = FakeSandbox(
            "@@SKILL\tbroken\n{not json\n@@END@@\n"
            "@@SKILL\tnolist\n{\"other\": 1}\n@@END@@\n"
            "@@SKILL\tgood\n{\"tools\": [{\"name\": \"a\"}]}\n@@END@@\n"
        )
        specs = self._scan(sb)
        self.assertEqual(list(specs), ["good"], "坏 JSON / 缺 tools 数组要跳过")

    def test_multiline_json(self):
        sb = FakeSandbox(
            '@@SKILL\tpretty\n{\n  "tools": [\n    {"name": "a"},\n    {"name": "b"}\n  ]\n}\n@@END@@\n'
        )
        specs = self._scan(sb)
        self.assertEqual(len(specs["pretty"]), 2, "多行 JSON 要能拼回来")

    def test_empty_output(self):
        self.assertEqual(self._scan(FakeSandbox("")), {})


class TestLoadSkillToolsFastPath(unittest.TestCase):
    def test_empty_only_skills_returns_without_any_exec(self):
        """按需披露 + 本会话还没读过技能 → 一次沙箱往返都不该发（省 4 秒）"""
        import src.agent.backends.sandbox as sb_mod

        from src.infra import SkillToolsService

        sb = FakeSandbox(_payload({"office": "{\"tools\": [{\"name\": \"t1\", \"command\": \"python3 {skill_dir}/run.py\"}]}"}))
        orig = sb_mod.sandbox_manager
        sb_mod.sandbox_manager = sb
        try:
            tools = SkillToolsService.load_skill_tools(f"t_unit_{uuid.uuid4().hex[:8]}",
                                                       only_skills=set())
        finally:
            sb_mod.sandbox_manager = orig
        self.assertEqual(tools, [])
        self.assertEqual(sb.calls, [], f"不该有任何 exec，实际: {sb.calls}")

    def test_unread_skill_is_filtered_out(self):
        import src.agent.backends.sandbox as sb_mod

        from src.infra import SkillToolsService

        # 必须带合法的 command：_make_tool 会校验命令模板，缺了它工具会被丢弃
        sb = FakeSandbox(_payload({
            "office": '{"tools": [{"name": "office_tool", "description": "d", "command": "python3 {skill_dir}/run.py"}]}',
            "rate": '{"tools": [{"name": "rate_tool", "description": "d", "command": "python3 {skill_dir}/run.py"}]}',
        }))
        orig = sb_mod.sandbox_manager
        sb_mod.sandbox_manager = sb
        try:
            tools = SkillToolsService.load_skill_tools(f"t_unit_{uuid.uuid4().hex[:8]}",
                                                       only_skills={"rate"})
        finally:
            sb_mod.sandbox_manager = orig
        names = [getattr(t, "name", "") for t in tools]
        self.assertIn("rate_tool", names, f"读过的技能应注册: {names}")
        self.assertNotIn("office_tool", names, f"没读过的技能不该注册: {names}")


class TestSpecCache(unittest.TestCase):
    """技能工具扫描结果的 Redis 缓存（每个新会话本来都要重扫一次，1~9 秒）"""

    def test_cache_hit_skips_sandbox(self):
        from src.infra import SkillToolsService

        sb = FakeSandbox(_payload({"x": '{"tools": [{"name": "t"}]}'}))  # 本该被解析
        orig_cache = sys.modules["src.infra.utils.CacheUtil"]
        stored: dict = {}

        class FakeCache:
            @staticmethod
            def get(k):
                return {"cached_skill": [{"name": "from_cache"}]}

            @staticmethod
            def set(k, v, ttl=0):
                stored[k] = v

        sys.modules["src.infra.utils"].CacheUtil = FakeCache
        try:
            specs = SkillToolsService._scan_skill_tools(sb, UNIT_USER)
        finally:
            sys.modules["src.infra.utils"].CacheUtil = orig_cache
        self.assertIn("cached_skill", specs, "命中缓存时应该直接用缓存")
        self.assertEqual(sb.calls, [], "命中缓存就不该再进沙箱")

    def test_cache_miss_scans_and_stores(self):
        from src.infra import SkillToolsService

        sb = FakeSandbox(_payload({"x": '{"tools": [{"name": "t"}]}'}))
        orig_cache = sys.modules["src.infra.utils"].CacheUtil
        stored: dict = {}

        class FakeCache:
            @staticmethod
            def get(k):
                return None

            @staticmethod
            def set(k, v, ttl=0):
                stored[k] = (v, ttl)

        sys.modules["src.infra.utils"].CacheUtil = FakeCache
        try:
            specs = SkillToolsService._scan_skill_tools(sb, UNIT_USER)
        finally:
            sys.modules["src.infra.utils"].CacheUtil = orig_cache
        self.assertEqual(list(specs), ["x"])
        self.assertIn("cache:skilltools:" + UNIT_USER, stored, "扫完要写回缓存")
        self.assertEqual(stored["cache:skilltools:" + UNIT_USER][1], 300, "TTL 应为 300 秒")


class TestTimingMarks(unittest.TestCase):
    def test_disabled_by_default_is_noop(self):
        """默认关闭：不启用时 mark() 返回 0，且不产生日志"""
        from src.infra.Timing import Marks, enabled

        if enabled():
            self.skipTest("环境里显式开了 PA_TIMING")
        mk = Marks("test")
        self.assertEqual(mk.mark("x"), 0.0)


if __name__ == "__main__":
    unittest.main()
