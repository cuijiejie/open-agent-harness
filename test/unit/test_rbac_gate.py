"""RBAC 工具装配的纯逻辑（不打网络，跑得快）

覆盖三类容易出事的规则：
  1. 没有角色 → 只拿到 always_on（含 MCP 的 always_on），拿不到业务工具
  2. 工具被管理员**停用**（pa_tools.enabled=0）→ 即使角色里有也不装配
  3. 工具被标记**公共**（pa_tools.always_on=1）→ 人人可用，**覆盖角色**（这是既定设计，
     但必须知道：它等于"给全员开权限"，配错了就是越权）
另外校验 ALWAYS_ON_NAMES 与注册表一致（写错名字会让"人人可用"静默失效）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NO_ROLE_USER = "t_gate_norole_probe"


class FakeToolMeta:
    """假的 pa_tools 元数据（flags: {name: (enabled, always_on)}）"""

    def __init__(self, flags: dict | None = None) -> None:
        self._flags = flags or {}

    def flags(self) -> dict:
        return dict(self._flags)

    def public_names(self) -> set[str]:
        return {k for k, (_e, a) in self._flags.items() if a}


class _patch_tool_meta:
    def __init__(self, flags: dict | None = None) -> None:
        self._fake = FakeToolMeta(flags)
        self._orig = None

    def __enter__(self):
        import src.infra.ToolMetaService as tms

        self._orig = tms.tool_meta
        tms.tool_meta = self._fake  # type: ignore[assignment]
        return self._fake

    def __exit__(self, *exc):
        import src.infra.ToolMetaService as tms

        tms.tool_meta = self._orig  # type: ignore[assignment]
        return False


class _ClearRbacCache(unittest.TestCase):
    """每个用例前清掉 RBAC 缓存（进程内 + Redis）

    踩过：allows_tool/resolve_tools 都走 permissions() 的缓存，前一个用例改过
    tool_meta 或角色后，后一个用例会读到"上一次的结果"，出现反向断言
    （无角色账号竟然 allow 某个业务工具、反而丢了 sandbox_shell）。
    """

    def setUp(self):
        try:
            from src.infra.RbacService import rbac

            rbac.invalidate()
        except Exception:
            pass


class TestResolveTools(_ClearRbacCache):
    def test_no_role_gets_only_always_on(self):
        from src.agent.tools.registry import ALWAYS_ON_NAMES, resolve_tools

        names = {getattr(t, "name", "") for t in resolve_tools(NO_ROLE_USER)}
        # 无角色 = 只有 always_on；其余一律不该出现（包括插件工具与 MCP 工具）
        extra = names - set(ALWAYS_ON_NAMES)
        self.assertFalse(extra, f"无角色不该有这些工具：{sorted(extra)}")
        for good in ALWAYS_ON_NAMES:
            self.assertIn(good, names, f"always_on 的 {good} 反而没了：{sorted(names)}")

    def test_disabled_tool_is_not_assembled(self):
        """全局停用的工具，角色里有也不装配"""
        from src.agent.tools.registry import resolve_tools

        with _patch_tool_meta({"sandbox_shell": (False, False)}):
            names = {getattr(t, "name", "") for t in resolve_tools(NO_ROLE_USER)}
        self.assertNotIn("sandbox_shell", names, f"停用的工具还在：{sorted(names)}")

    def test_always_on_flag_overrides_role(self):
        """公共标记会覆盖角色 —— 已定设计，但配错等于给全员开权限"""
        from src.agent.tools.registry import resolve_tools

        with _patch_tool_meta({"sandbox_shell": (True, True)}):
            names = {getattr(t, "name", "") for t in resolve_tools(NO_ROLE_USER)}
        self.assertIn("sandbox_shell", names, "always_on 应该覆盖角色限制")

    def test_admin_gets_product_tools(self):
        """管理员（admin 角色 tools 含产品工具）应能拿到"""
        from src.agent.tools.registry import resolve_tools
        from src.infra.RbacService import rbac

        perm = rbac.permissions("alice")
        if "*" not in perm["tools"] and "sandbox_shell" not in perm["tools"]:
            self.skipTest("当前环境的 admin 角色没有 always_on 工具")
        names = {getattr(t, "name", "") for t in resolve_tools("alice")}
        self.assertIn("sandbox_shell", names, f"管理员缺少 always_on 工具：{sorted(names)}")


class TestAllowsTool(_ClearRbacCache):
    def test_no_role_denies_business_tools(self):
        """无角色 = 只有 always_on；插件/业务工具一律不给"""
        from src.infra.RbacService import rbac

        self.assertFalse(rbac.allows_tool(NO_ROLE_USER, "demo_say_hello"))

    def test_no_role_allows_always_on(self):
        from src.infra.RbacService import rbac

        self.assertTrue(rbac.allows_tool(NO_ROLE_USER, "sandbox_shell"))
        self.assertTrue(rbac.allows_tool(NO_ROLE_USER, "ask_user"))

    def test_unknown_tool_denied(self):
        from src.infra.RbacService import rbac

        self.assertFalse(rbac.allows_tool(NO_ROLE_USER, "definitely_not_a_tool"))

    def test_admin_wildcard(self):
        from src.infra.RbacService import rbac

        perm = rbac.permissions("alice")
        if "*" not in perm["tools"]:
            self.skipTest("admin 不是通配符权限")
        self.assertTrue(rbac.allows_tool("alice", "sandbox_shell"))


class TestNoFailOpenSwitch(_ClearRbacCache):
    """`rbac_enabled=false` 不再是后门（2026-11-03 移除）

    旧实现：开关一关 → allows_tool 恒 True、is_admin 恒 True（人人管理员）。
    现在：开关废掉，权限只认角色；配置写 false 也不放行。
    """

    def test_enabled_is_always_true(self):
        from config.Configs import cfg

        from src.infra.RbacService import rbac

        orig = getattr(cfg, "rbacEnabled", True)
        try:
            cfg.rbacEnabled = False          # 模拟"有人把开关关了"
            self.assertTrue(rbac.enabled, "rbac_enabled=false 不能把整体关掉")
        finally:
            cfg.rbacEnabled = orig

    def test_switch_off_does_not_make_anyone_admin(self):
        from config.Configs import cfg

        from src.infra.RbacService import rbac

        orig = getattr(cfg, "rbacEnabled", True)
        try:
            cfg.rbacEnabled = False
            rbac.invalidate()
            self.assertFalse(rbac.is_admin("t_switch_probe"),
                             "开关关掉后普通人不能变成管理员")
        finally:
            cfg.rbacEnabled = orig
            rbac.invalidate()

    def test_switch_off_does_not_grant_write_tools(self):
        from config.Configs import cfg

        from src.agent.tools.registry import resolve_tools
        from src.infra.RbacService import rbac

        orig = getattr(cfg, "rbacEnabled", True)
        try:
            cfg.rbacEnabled = False
            rbac.invalidate()
            self.assertFalse(rbac.allows_tool("t_switch_probe", "demo_say_hello"),
                             "开关关掉后不能给普通账号业务工具权限")
            names = {getattr(t, "name", "") for t in resolve_tools("t_switch_probe")}
            self.assertNotIn("demo_say_hello", names, f"装配清单里不该有业务工具: {sorted(names)}")
        finally:
            cfg.rbacEnabled = orig
            rbac.invalidate()


class TestRegistryConsistency(_ClearRbacCache):
    def test_always_on_names_exist_in_registry(self):
        from src.agent.tools.registry import ALWAYS_ON_NAMES, REGISTRY

        names = {s.name for s in REGISTRY}
        missing = [n for n in ALWAYS_ON_NAMES if n not in names]
        self.assertEqual(missing, [], f"ALWAYS_ON_NAMES 里写了不存在的工具: {missing}")


if __name__ == "__main__":
    unittest.main()
