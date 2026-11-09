"""工具注册表：声明式定义每个工具的风险等级与可见范围（谁能用）

为什么要注册表
--------------
原先 `ALL_TOOLS` 是硬编码 list，**所有登录用户拿到完全相同的工具清单** ——
意味着任何能登录的人都能调用写类工具（点一下「批准」就改业务数据）。
审批门管的是"确认"，不是"授权"；真正该拦的是"这个人根本不该有这个工具"。

现在每个工具声明成 `ToolSpec`：
    name   工具名（必须与 @tool 定义一致）
    tool   工具对象
    risk   read（只读查询）| write（改业务数据）| exec（在沙箱里执行）
    scope  显式可见范围（留空 = 按 risk 的默认策略）

装配时 `resolve_tools(username)` 按 scope 过滤 —— 没权限的工具**根本不进模型的
工具清单**（比"调用时再拒绝"更安全：提示词里都没有，模型不会尝试、也不会幻觉它会）

配置（configs.cfg 的 [agent] 段）
--------------------------------
    tool_admin_users=alice,bob   ; 管理员用户名（逗号分隔）
    tool_write_scope=all|admins|none|用户1,用户2   ; 写类工具给谁（默认 all，见下）
    tool_exec_scope=all|admins|none|用户1,用户2    ; 沙箱执行类给谁（默认 all）

**默认值刻意保守但兼容**：写类默认 `all`（保持现有行为不变），同时在装配时打
WARNING 提示去收紧；生产的 cfg 里可以直接写 `tool_write_scope=admins` 并填好管理员。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.infra.utils.LogUtil import log


@dataclass
class ToolSpec:
    """一个工具的声明（名字 / 对象 / 风险等级 / 可见范围）"""

    name: str
    tool: Any
    risk: str = "read"          # read | write | exec
    scope: str = ""             # 空 = 按 risk 默认策略
    note: str = ""              # 审计用的一句话说明
    extra: dict = field(default_factory=dict)


def _scope_allows(scope: str, admins: set[str], username: str) -> bool:
    """判断 scope 是否放行该用户。scope 取值：all / admins / none / 逗号分隔的用户名"""
    v: str = (scope or "").strip()
    if not v or v.lower() == "all":
        return True
    if v.lower() == "none":
        return False
    if v.lower() == "admins":
        return username in admins
    return username in {x.strip() for x in v.split(",") if x.strip()}


def _default_scope(risk: str) -> str:
    """按风险等级取默认策略（写类默认 all 以保持兼容，但会打 WARNING）"""
    from config.Configs import cfg

    if risk == "write":
        return str(getattr(cfg, "toolWriteScope", "all") or "all")
    if risk == "exec":
        return str(getattr(cfg, "toolExecScope", "all") or "all")
    return "all"


def _admins() -> set[str]:
    from config.Configs import cfg

    raw: str = str(getattr(cfg, "toolAdminUsers", "") or "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def resolve_tools(username: str) -> list:
    """按授权过滤后的工具清单（保持注册顺序 = 给模型的清单顺序）

    **只按角色过滤**（2026-11-03 起）：以前还有一条"RBAC 关闭就回落到
    tool_admin_users / tool_write_scope"的分支，而 `tool_write_scope=all`
    等于人人可写 —— 配置一行就能全员提权，已删除。
    现在即使 `rbac_enabled=false` 也只按角色判定（RbacService.enabled 恒为 True）。
    """
    from src.infra.RbacService import rbac

    perm: dict = rbac.permissions(username)
    from src.infra.ToolMetaService import tool_meta

    flags: dict = tool_meta.flags()          # {name: (enabled, always_on)}，读不到就是全放行
    allowed: list = []
    denied: list[str] = []
    for spec in REGISTRY:
        m_enabled, m_always = flags.get(spec.name, (True, False))
        if not m_enabled:
            denied.append(f"{spec.name}(已停用)")
            continue
        if m_always or perm["tools"] == "*" or spec.name in perm["tools"]:
            allowed.append(spec.tool)
        else:
            denied.append(f"{spec.name}(roles={','.join(perm['roles'])})")
    log.info(
        f"RBAC 工具装配: user={username}, 角色={perm['roles']}, "
        f"管理员={perm['is_admin']}, 可用={len(allowed)}, 隐藏={denied}"
    )
    return allowed


# 人人默认可用、不参与角色分配的基础工具（唯一来源：RbacService 也从这里取）
ALWAYS_ON_NAMES: tuple = (
    "ask_user", "sandbox_shell",
    "remember_preference", "forget_preference",   # 偏好：人人可用
    "remember_fact", "forget_fact",               # 业务知识：人人可用
)

# MCP 侧的人人可用工具（不进角色页，装配时强制并入）
# 默认没有「人人可用」的 MCP 工具；接入自己的 MCP 服务后，
# 把希望全员可用的工具名填进来（或在 pa_tools 里把 always_on 置 1）。
ALWAYS_ON_MCP_NAMES: tuple = ()


_META_SYNCED: bool = False


def _raw_specs() -> list[dict]:
    """代码里声明的原始元数据（用于同步进 pa_tools）"""
    return [
        {"name": s.name, "risk": s.risk, "note": s.note,
         "group": str(s.extra.get("group") or ""),
         # 可选：代码里给个中文名，只在**首次插库**时用（管理员改过的不覆盖）
         "display_name": str(s.extra.get("display_name") or ""),
         "always_on": s.name in ALWAYS_ON_NAMES}
        for s in REGISTRY
    ]


def describe() -> list[dict]:
    """工具清单（代码声明 + 数据库覆盖），供角色页/内置工具页使用

    - 代码里有没有这个工具 = 真实能力（发版决定）
    - 说明 / 分组 / 风险 / 停用 / 人人可用 = 数据库元数据（管理员在页面改）
    """
    global _META_SYNCED
    try:
        from src.infra.ToolMetaService import tool_meta

        if not _META_SYNCED:
            tool_meta.sync(_raw_specs())        # 只在本进程首次调用时同步，幂等
            _META_SYNCED = True
        meta: dict = tool_meta.all()
    except Exception as e:                       # 元数据不可用不影响工具本身
        log.warning(f"读取工具元数据失败（回落到代码默认）: {e}")
        meta = {}

    admins: set[str] = _admins()
    out: list[dict] = []
    for spec in REGISTRY:
        scope: str = spec.scope or _default_scope(spec.risk)
        m: dict = meta.get(spec.name) or {}
        out.append({
            "name": spec.name,
            "display_name": m.get("display_name") or spec.name,
            "grp": m.get("grp") or str(spec.extra.get("group") or "其他"),
            "risk": m.get("risk") or spec.risk,
            "scope": scope,
            "resolve": sorted(admins) if scope == "admins" else scope,
            "note": (m.get("note") if m.get("note") else spec.note),
            "always_on": bool(m.get("always_on")) if m else (spec.name in ALWAYS_ON_NAMES),
            "enabled": bool(m.get("enabled", True)),
            "ghost": bool(m.get("ghost")),
        })
    # 排序：人人可用的靠前，然后按分组
    out.sort(key=lambda x: (0 if x["always_on"] else 1, str(x["grp"]), str(x["name"])))
    return out


def load_registry() -> list[ToolSpec]:
    """把各业务模块里的 @tool 装配成 ToolSpec 列表（由 tools/__init__ 调用）"""
    from src.agent.tools.interaction import ask_user
    from src.agent.tools.knowledge_tools import forget_fact, remember_fact
    from src.agent.tools.kb_tools import kb_search
    from src.agent.tools.prefs import forget_preference, remember_preference
    from src.agent.tools.sandbox import sandbox_shell

    return [
        ToolSpec("ask_user", ask_user, "read", note="向用户提问/给选项",
                 extra={"group": "交互", "display_name": "向用户提问/给选项"}),
        ToolSpec("sandbox_shell", sandbox_shell, "exec", note="在本人沙箱执行命令",
                 extra={"group": "沙箱", "display_name": "沙箱执行命令"}),
        ToolSpec("remember_preference", remember_preference, "write", note="记住用户长期偏好",
                 extra={"group": "记忆", "display_name": "记住用户偏好"}),
        ToolSpec("remember_fact", remember_fact, "write", note="记住业务知识（长期）",
                 extra={"group": "记忆", "display_name": "记住业务知识"}),
        ToolSpec("forget_fact", forget_fact, "write", note="忘掉某条业务知识",
                 extra={"group": "记忆", "display_name": "忘掉业务知识"}),
        ToolSpec("forget_preference", forget_preference, "write", note="忘掉用户偏好",
                 extra={"group": "记忆", "display_name": "忘掉用户偏好"}),
        ToolSpec("kb_search", kb_search, "read", note="检索知识库文档（RAG）",
                 extra={"group": "知识库", "display_name": "知识库检索"}),
    ]


def _with_plugins(base: list) -> list:
    """内置工具 + 插件工具。插件加载失败不影响内核。"""
    try:
        from src.agent.tools.plugin_loader import load_plugin_specs

        extra = load_plugin_specs()
        names = {s.name for s in base}
        base = base + [s for s in extra if s.name not in names]
    except Exception as e:
        try:
            from src.infra.utils.LogUtil import log
            log.warning(f"插件加载失败（忽略）: {e}")
        except Exception:
            pass
    return base


REGISTRY: list[ToolSpec] = _with_plugins(load_registry())

__all__ = ["ToolSpec", "REGISTRY", "resolve_tools", "describe"]
