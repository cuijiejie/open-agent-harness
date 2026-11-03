"""技能自带工具：把技能包里的 `tools.json` 变成模型可调用的工具

它解决什么问题
--------------
现在「技能」只有说明书（SKILL.md）：模型要知道怎么拼 `python3 /sandbox/skills/xxx/scripts/y.py
--input ... --classes ...` 才能用上技能里的脚本。参数一多，模型就会漏传/拼错/照抄例子。

技能带工具之后：技能包声明 `tools.json`，模型的工具清单里**直接多出**一个工具
（如 `analysis_report(input_file=..., classes=...)`），只填参数，命令行由技能包固定。

安全红线（极重要）
----------------
**技能里的代码绝不在应用进程里执行** —— 否则等于"谁上传技能谁就能在服务器上跑任意代码"。
本模块只做三件事：
    ① 读取并校验技能包里的 `tools.json`（沙箱内读取）
    ② 生成工具声明（给模型看的名字/说明/参数 schema）
    ③ 调用时把参数代入命令模板，**交给 SandboxManager.exec 在该用户的沙箱里执行**
宿主侧永远只是"拼命令 + 收结果"，不执行任何技能代码。

包格式（放在技能目录里，与 SKILL.md 并列）
----------------------------------------
    /sandbox/skills/<技能名>/
    ├── SKILL.md
    ├── tools.json          ← 本模块读它
    └── scripts/xxx.py

    {
      "tools": [
        {
          "name": "analysis_report",
          "description": "按班级口径生成学生成绩分析报告（HTML + 图表）",
          "command": "python3 {skill_dir}/scripts/analyze.py --input {input_file} --classes {classes}",
          "parameters": [
            {"name": "input_file", "type": "string", "required": true,
             "description": "沙箱内的 Excel 路径，如 /sandbox/学生表.xlsx"},
            {"name": "classes", "type": "string", "required": false, "default": "",
             "description": "班级名，逗号分隔；留空则分析全部"}
          ]
        }
      ]
    }

· `{skill_dir}` 是内置占位符，自动替换成该技能在沙箱里的绝对路径
· 其它 `{xxx}` 必须与 parameters 里声明过的名字一一对应；出现未声明的占位符 → 该工具不加载
· 命令入口白名单：python3/python/bash/sh、node/npx/tsc、java/javac、go、rustc/cargo、gcc/g++/make
  （镜像 pa-sandbox:1.3 起这些运行时都已预装；其它入口一律拒绝）
· 所有参数值都用 shlex.quote 转义后才代入，避免拼命令时被注入
"""
from __future__ import annotations

import json
import re
import shlex
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import Field, create_model

from src.infra.utils.LogUtil import log

SKILLS_DIR: str = "/sandbox/skills"
TOOLS_FILE: str = "tools.json"

MAX_SKILLS: int = 20            # 每个用户最多扫描这么多个技能目录
MAX_TOOLS_PER_SKILL: int = 10   # 单个技能最多声明这么多工具
_SPEC_CACHE_PREFIX: str = "cache:skilltools:"
_SPEC_CACHE_TTL: int = 300
MAX_NAME_LEN: int = 40
MAX_DESC_LEN: int = 500
MAX_OUTPUT_CHARS: int = 8000

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,%d}$" % MAX_NAME_LEN)
_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_ALLOWED_PREFIX = (
    "python3 ", "python ", "bash ", "sh ", "/usr/bin/python3 ",
    # 1.3 镜像起支持的语言运行时（沙箱内已预装；仍只允许这些入口，其它一律拒绝）
    "node ", "npx ", "tsc ", "java ", "javac ", "go ",
    "rustc ", "cargo ", "gcc ", "g++ ", "make ",
)
_PY_TYPES: dict[str, Any] = {
    "string": str, "integer": int, "number": float, "boolean": bool,
}


def _validate_command(command: str, declared: set[str]) -> tuple[bool, str]:
    """校验命令模板：入口白名单 + 占位符必须在声明范围内"""
    cmd: str = (command or "").strip()
    if not cmd:
        return False, "command 为空"
    if not cmd.startswith(_ALLOWED_PREFIX):
        return False, f"command 必须以 {_ALLOWED_PREFIX} 之一开头"
    placeholders: set[str] = set(_PARAM_RE.findall(cmd))
    unknown: set[str] = placeholders - declared - {"skill_dir"}
    if unknown:
        return False, f"命令里出现未声明的占位符: {sorted(unknown)}"
    return True, ""


def _build_args_schema(tool_name: str, params: list[dict]) -> Any:
    """按 parameters 动态生成 pydantic 参数模型（模型据此填参数）"""
    fields: dict[str, Any] = {}
    for p in params:
        pname: str = str(p.get("name") or "").strip()
        ptype = _PY_TYPES.get(str(p.get("type") or "string").lower(), str)
        desc: str = str(p.get("description") or "")[:200]
        if p.get("required"):
            fields[pname] = (ptype, Field(..., description=desc))
        else:
            fields[pname] = (ptype, Field(p.get("default", "" if ptype is str else None),
                                          description=desc))
    return create_model(f"{tool_name}_args", **fields) if fields else None


def _make_tool(sandbox: Any, username: str, skill: str, skill_dir: str, spec: dict) -> BaseTool | None:
    """把一条工具声明变成可调用的 StructuredTool（执行动作发生在沙箱内）"""
    name: str = str(spec.get("name") or "").strip()
    if not _NAME_RE.match(name):
        log.warning(f"技能工具名不合法，跳过: skill={skill}, name={name!r}")
        return None

    description: str = str(spec.get("description") or "").strip()[:MAX_DESC_LEN]
    if not description:
        description = f"{skill} 技能提供的工具 {name}"

    params: list[dict] = [p for p in (spec.get("parameters") or []) if isinstance(p, dict)]
    declared: set[str] = {str(p.get("name") or "") for p in params if p.get("name")}
    ok, why = _validate_command(str(spec.get("command") or ""), declared)
    if not ok:
        log.warning(f"技能工具命令不合法，跳过: skill={skill}, name={name}, 原因={why}")
        return None

    command: str = str(spec["command"]).strip()

    def _run(**kwargs: Any) -> str:
        cmd: str = command.replace("{skill_dir}", shlex.quote(skill_dir))
        for p in params:
            pname: str = str(p.get("name") or "")
            if not pname:
                continue
            value: Any = kwargs.get(pname, p.get("default", ""))
            if value is None:
                value = ""
            cmd = cmd.replace("{" + pname + "}", shlex.quote(str(value)))
        if "{" in cmd:
            return f"调用失败：命令模板还有未替换的占位符（{cmd[:120]}）"
        log.info(f"技能工具调用: user={username}, skill={skill}, tool={name}, cmd={cmd[:200]}")
        res = sandbox.exec(username, cmd, timeout=getattr(sandbox, "timeout", 30))
        out: str = (res.stdout or "").strip()
        err: str = (res.stderr or "").strip()
        if not res.ok and not out:
            return f"执行失败（返回码 {res.returncode}）：{err[:500] or '无输出'}"
        body: str = out or err
        if len(body) > MAX_OUTPUT_CHARS:
            body = body[:MAX_OUTPUT_CHARS] + f"\n…（输出已截断，共 {len(body)} 字符）"
        return body or "（命令执行成功，但没有任何输出）"

    schema = _build_args_schema(name, params)
    return StructuredTool.from_function(
        func=_run,
        name=name,
        description=f"[{skill} 技能] {description}",
        args_schema=schema,
    )


def load_skill_tools(username: str, only_skills: set[str] | None = None) -> list[BaseTool]:
    """扫描该用户沙箱里的技能，返回它们声明的工具（失败一律跳过，不影响对话）

    only_skills:
        None  = 全量注册（默认，行为与之前一致）
        set() = 只注册这些技能的工具；**空集 = 一个都不注册**
                —— 配合"按需披露"，只有模型读过 SKILL.md 的技能才会露出工具
    """
    tools: list[BaseTool] = []
    try:
        from src.agent.backends.sandbox import sandbox_manager as sandbox

        if not sandbox.enabled or not username:
            return tools

        # 按需披露且本会话还没读过任何技能 → 一个工具都不该注册。
        # 这里必须**提前返回**：下面的 ls 是一次沙箱往返（实测 1~4 秒），
        # 新会话第一句白等这 4 秒纯属浪费（dev 默认 lazy=true，每个新会话都撞上）。
        if only_skills is not None and not only_skills:
            return tools

        specs: dict[str, list] = _scan_skill_tools(sandbox, username)
        if not specs:
            return tools
        names: list[str] = [n for n in specs if only_skills is None or n in only_skills]
        if not names:
            return tools

        seen: set[str] = set()
        for skill in names:
            skill_dir: str = f"{SKILLS_DIR}/{skill}"
            for item in specs[skill][:MAX_TOOLS_PER_SKILL]:
                if not isinstance(item, dict):
                    continue
                tool = _make_tool(sandbox, username, skill, skill_dir, item)
                if tool is None:
                    continue
                if tool.name in seen or tool.name in _RESERVED_TOOL_NAMES:
                    log.warning(f"技能工具重名/与内置工具冲突，跳过: {tool.name}（skill={skill}）")
                    continue
                seen.add(tool.name)
                tools.append(tool)

        if tools:
            log.info(f"技能自带工具已加载: user={username}, {[t.name for t in tools]}")
    except Exception as e:
        log.warning(f"加载技能自带工具失败（不影响对话）: user={username}, err={e}")
    return tools


def _scan_skill_tools(sandbox: Any, username: str) -> dict[str, list]:
    """**一次**沙箱往返把「技能名 → tools 列表」全拿到（结果缓存 300 秒）

    原来是一个技能一次 `cat tools.json`（N+1 次往返，每次 1~3.5 秒，
    用户有 5 个技能就要 9 秒）。现在一条命令里把每个技能的 tools.json
    用 @@SKILL/@ @END 包起来输出，Python 侧切分解析。

    再叠一层 Redis 缓存：技能清单变化频率很低，而**每个新会话**都要扫一次
    （实测一次 exec 1~9 秒，是"新会话第一句慢"的固定成本之一）。
    技能增删/启停会调 _bust_skills_cache → 清掉 cache:skilltools:*。

    返回 {"技能名": [tool dict, ...]}；解析失败的技能直接跳过（不影响其它技能）。
    """
    cache_key: str = _SPEC_CACHE_PREFIX + username
    try:
        from src.infra.utils import CacheUtil

        hit = CacheUtil.get(cache_key)
        if isinstance(hit, dict):
            return hit
    except Exception:
        pass
    cmd = (
        f"for d in {SKILLS_DIR}/*/; do "
        "n=${d%/}; n=${n##*/}; f=\"${d}tools.json\"; "
        "[ -f \"$f\" ] || continue; "
        "printf '@@SKILL\\t%s\\n' \"$n\"; cat \"$f\" 2>/dev/null; printf '\\n@@END@@\\n'; "
        "done | head -4000"
    )
    res = sandbox.exec(username, cmd, timeout=25)
    out: str = res.stdout or ""
    specs: dict[str, list] = {}
    cur: str | None = None
    buf: list[str] = []
    for line in out.splitlines():
        if line.startswith("@@SKILL\t"):
            cur = line.split("\t", 1)[1].strip()
            buf = []
        elif line.strip() == "@@END@@":
            if cur and len(specs) < MAX_SKILLS:
                try:
                    spec: Any = json.loads("\n".join(buf).strip() or "{}")
                except json.JSONDecodeError as e:
                    log.warning(f"技能 tools.json 不是合法 JSON，跳过: skill={cur}, err={e}")
                    cur, buf = None, []
                    continue
                items = spec.get("tools") if isinstance(spec, dict) else None
                if isinstance(items, list):
                    specs[cur] = items
                else:
                    log.warning(f"技能 tools.json 缺少 tools 数组，跳过: skill={cur}")
            cur, buf = None, []
        elif cur is not None:
            buf.append(line)
    try:
        from src.infra.utils import CacheUtil

        CacheUtil.set(cache_key, specs, _SPEC_CACHE_TTL)
    except Exception:
        pass
    return specs


# 与内置工具重名的技能工具一律不加载（避免把 agent 的核心能力遮住）
_RESERVED_TOOL_NAMES: set[str] = {
    "ask_user", "sandbox_shell",
    "ls", "read_file", "write_file", "edit_file", "glob", "grep", "task",
}


__all__ = ["load_skill_tools", "SKILLS_DIR", "TOOLS_FILE"]
