"""
聊天服务：编排会话管理、agent 调用、消息持久化、历史裁剪、中断恢复。

核心流程（流式）:
  ① 确保会话存在（MySQL）
  ② 持久化用户消息（MySQL）
  ③ 获取/创建 agent（缓存复用，checkpointer=PostgresSaver）
  ④ 常规路径构建输入并流式执行；恢复路径 Command(resume=...) 继续
  ⑤ SSE 逐块返回内容
  ⑥ 检测 ask_user interrupt：有则发 options 事件并暂停等用户选择
  ⑦ 裁剪历史、持久化 agent 响应到 MySQL

TODO: 升级 langgraph >= 1.3 后将流式处理从 agent.stream(stream_mode="messages")
      迁移到 agent.stream_events(version="v3")，使用 stream.messages 投影替代手工 chunk 解析。
TODO: 当前 reasoning_content 通过 ChatOpenAIWithReasoning 子类保留在 additional_kwargs 中，
      流式层跳过展示（不输出到 SSE），但 LangGraph state 中保留以支持多轮推理链连续性。
"""
import json
import os
import re as _re
import threading
import time
import asyncio
import base64
import urllib.request
import urllib.error
from typing import Any, AsyncGenerator, Generator

from src.infra.db.database import session_scope

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from langchain_core.messages import HumanMessage, AIMessage

from src.agent.middlewares.FixToolNameMiddleware import HallucinationLoopError
from src.infra.utils.LogUtil import log
from src.infra.dao import MessageDao, SessionDao
from src.agent.main_agent import create_agent
from config.Configs import cfg


# 上下文治理的预算在配置里：[agent] context_char_budget（默认 60000 字符）
# 治理逻辑见 service/ContextService.py（先压工具输出 → 再滚动摘要 → 最后才丢）



# 后台收尾任务的强引用集合：asyncio 只保留弱引用，不记住就可能被 GC 掉
_POST_TASKS: set = set()


def _spawn_post_turn(agent: CompiledStateGraph, config: dict[str, Any],
                     session_id: str, username: str, acc: dict) -> None:
    """把「上下文治理 + 技能缓存清理」挂成后台 task（替代原来的后台线程）

    为什么不再用线程：
    1. 异步化后治理里的模型调用/DB 都是 await，占一个 task 而不是一个线程；
    2. `AsyncPostgresSaver` 的连接池**绑定创建它的事件循环**，从别的线程
       `asyncio.run(...)` 会直接报 "Future attached to a different loop"。
    """

    async def _run() -> None:
        from src.infra.db.database import session_scope

        try:
            # 请求级 session 在响应结束时会被中间件关掉，这里用独立 session
            async with session_scope():
                await _govern_context(agent, config, session_id, username)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning(f"上下文治理失败（忽略，下一轮还会再治理）: {e}")
        try:
            if acc.get("skills_touched"):
                from src.agent.main_agent import evict_agent_from_cache

                evict_agent_from_cache(session_id, username)
                log.info(f"技能目录有改动，已清理 agent 缓存（下一轮重新加载技能工具）: user={username}")
        except Exception as e:
            log.warning(f"清理 agent 缓存失败（技能工具需新会话生效）: {e}")

    try:
        task = asyncio.create_task(_run(), name=f"post-turn-{(session_id or '')[:8]}")
        _POST_TASKS.add(task)
        task.add_done_callback(_POST_TASKS.discard)
    except RuntimeError as e:      # 没有运行中的事件循环（脚本/测试）→ 同步跑完
        log.warning(f"无法创建后台任务，改为同步执行治理: {e}")
        try:
            asyncio.run(_run())
        except Exception as e2:
            log.warning(f"上下文治理失败（忽略）: {e2}")


async def _finish_turn(acc: dict, username: str, session_id: str, t0: float) -> None:
    """本轮收尾：指标 + 轨迹。**幂等**——多个 done 分支都调用，只记一次。

    为什么需要：stream 是生成器，客户端收到 done 就断流、生成器被关闭，
    写在 done 之后的代码永远不会执行。所以必须在每个 done 之前调用本函数。
    """
    if acc.get("_finished"):
        return
    acc["_finished"] = True

    # 轨迹：本轮助手的最终回复（含工具调用次数，便于对照）
    try:
        from src.infra import TraceService

        _ans: str = str(acc.get("text") or "").strip()
        if _ans:
            await asyncio.to_thread(TraceService.record, username=username, session_id=session_id, kind="assistant",
                                name="助手回复", args="", result=_ans, status="ok",
                                elapsed_ms=int((time.time() - t0) * 1000),
                                turn_no=int(acc.get("turn_no") or 0))
    except Exception as _te:
        log.warning(f"轨迹记录助手回复失败（忽略）: {_te}")

    # 交付物归拢：不管模型写在哪，用户只管 /out（生成）和 /in（上传）两组。
    # 确定性执行，不依赖模型自觉；mv -n 不覆盖同名文件。
    # ★ 只有本轮**调过工具**才做：这里 3 条 find+mv 沙箱命令（timeout 25/15/25 秒）
    #   是 done 之前跑的，"你好"这种纯聊天也跑一遍 → 每轮白等约 10 秒，
    #   前端表现就是「回答完了一直转圈」。
    _touched: bool = bool(acc.get("trace_tools") or acc.get("tools"))
    try:
        from src.agent.backends.sandbox import SandboxManager

        _sb = SandboxManager()
        if _sb.enabled and _touched:
            await _sb.aexec(
                username,
                "mkdir -p /sandbox/output && cd /sandbox && "
                r"find . -maxdepth 1 -type f \( -name '*.xlsx' -o -name '*.xls' -o -name '*.docx' "
                r"-o -name '*.pptx' -o -name '*.pdf' -o -name '*.html' -o -name '*.png' "
                r"-o -name '*.jpg' -o -name '*.md' -o -name '*.zip' -o -name '*.csv' \) "
                r"-exec mv -n -t /sandbox/output/ {} \; 2>/dev/null; true",
                timeout=25,
            )
            # 产物目录去污染：模型爱把脚本也写进 output/，统一挪到 work/scripts/
            await _sb.aexec(
                username,
                "mkdir -p /sandbox/work/scripts && "
                r"find /sandbox/output -type f \( -name '*.py' -o -name '*.sh' \) "
                r"-exec mv -n -t /sandbox/work/scripts/ {} \; 2>/dev/null; true",
                timeout=15,
            )

            # 中间产物（脚本/日志/json/csv/txt）扫进 .tmp/，根目录保持干净
            await _sb.aexec(
                username,
                "mkdir -p /sandbox/.tmp && cd /sandbox && "
                r"find . -maxdepth 1 -type f \( -name '*.py' -o -name '*.json' -o -name '*.jsonl' "
                r"-o -name '*.csv' -o -name '*.tsv' -o -name '*.log' -o -name '*.txt' "
                r"-o -name '*.sh' -o -name '*.ipynb' -o -name '*.yaml' -o -name '*.yml' \) "
                r"-exec mv -n -t /sandbox/.tmp/ {} \; 2>/dev/null; true",
                timeout=25,
            )
    except Exception as _ce:
        log.warning(f"交付物归拢失败（忽略）: {_ce}")
    try:
        from src.infra.MetricsService import record_turn, record_trace
        _dur = round(time.time() - t0, 2)
        _model = str(getattr(cfg, "deepseekModel", "") or "")
        record_turn(
            user=username, session=session_id, model=_model, duration_s=_dur,
            # 注意：正文累加在 acc["text"]（不是 "chars"，那是历史遗留字段）——
            # 之前读错 key，指标里 content_chars 恒为 0。
            content_chars=len(str(acc.get("text") or "")),
            reasoning_chars=int(acc.get("reasoning") or 0),
            # 工具调用**次数**用 calls 计数；tool_names 是去重后的名字集合，只适合看用了哪些
            tool_calls=int(acc.get("calls") or 0),
            tool_errors=int(acc.get("tool_errors") or 0),
            budget_hit=bool(acc.get("budget_hit")),
            tokens_in=acc.get("tokens_in"), tokens_out=acc.get("tokens_out"),
        )
        record_trace(
            user=username, session=session_id, model=_model, duration_s=_dur,
            tools=list(acc.get("trace_tools") or []),
            tokens_in=acc.get("tokens_in"), tokens_out=acc.get("tokens_out"),
            budget_hit=bool(acc.get("budget_hit")),
        )
    except Exception as e:
        log.warning(f"收尾记录失败（忽略）: {e}")


# ========== 过程展示（思考 + 工具步骤） ==========
# 设计要点：**只推给前端展示，绝不入库**。
# · 思考（reasoning_content）逐片推 {"reasoning": "..."}，前端渲染成可折叠的「思考过程」块；
#   刷新页面/换设备后从 MySQL 读的历史里不含它 —— 因为入库只走
#   MessageDao.create_message(..., full_response) 这一条路，思考从不进该路径。
# · 工具调用推 {"step": {...}}，前端渲染成过程卡片（正在查产品 / 正在沙箱执行命令…）。
_TOOL_LABELS: dict[str, str] = {
    "sandbox_shell": "在沙箱执行命令",
    "ask_user": "等待你的选择",
    "read_file": "读取文件",
    "write_file": "写入文件",
    "edit_file": "修改文件",
    "ls": "查看目录",
    "glob": "查找文件",
    "grep": "搜索内容",
}

# 从工具参数里挑一条最适合给用户看的说明（命令 / 文件 / 产品…），按顺序优先取第一个有值的
_DETAIL_KEYS: tuple[str, ...] = (
    "command", "file_path", "path", "query", "keyword", "pattern", "file", "name",
)

# 单轮推给前端的思考上限（超出只计数不再推，防止刷屏）
_MAX_REASONING_PUSH: int = 20000


def _tool_label(name: str) -> str:
    """工具名 → 给用户看的中文说明（未知工具原样显示）"""
    return _TOOL_LABELS.get(name, name or "工具调用")


# 允许跳转的 URL 前缀：前端拿到就用 window.open / a.click 打开，
# 不做协议白名单的话 `javascript:` / `data:` 会在**本站同源**执行脚本（拿用户会话调 API）。
_SAFE_NAV_PREFIXES: tuple[str, ...] = ("http://", "https://", "/")


def _is_safe_nav_url(url: str) -> bool:
    """导航 URL 白名单（后端这道是兜底，前端还会再挡一次）

    `//evil.com` 是**协议相对外链**：浏览器会补成 https://evil.com —— 判断
    `startswith("/")` 会把它放过去，因此必须单独排除。前端那份实现早就排除了，
    后端这里之前漏了（两边逻辑不一致本身就是隐患）。
    """
    u: str = (url or "").strip().lower()
    if not u:
        return False
    if u.startswith("//"):
        return False
    return u.startswith(_SAFE_NAV_PREFIXES)


def _tool_detail(args_json: str) -> str:
    """从工具参数 JSON（流式拼接所得）里抽一条简短说明，解析失败返回空串

    流式 tool_call 的参数是**残缺 JSON**（分片拼接，可能缺右括号），因此
    json.loads 失败时用正则兜底把值捞出来 —— 否则前端只能显示光秃秃的工具名。
    """
    if not args_json or not args_json.strip():
        return ""
    data: Any = None
    try:
        data = json.loads(args_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        data = None
    cands: list = []
    if isinstance(data, dict):
        cands = [data.get(k) for k in _DETAIL_KEYS]
        cands += [v for v in data.values() if isinstance(v, str)]
    else:
        # 兜底：从残缺 JSON 里按 key 顺序正则取值
        for _k in _DETAIL_KEYS:
            _m = _re.search(r'"' + _k + r'"\s*:\s*"([^"\\]{1,200})', args_json)
            if _m:
                cands.append(_m.group(1))
    for v in cands:
        if isinstance(v, str) and v.strip():
            s: str = " ".join(v.split())
            return s[:80] + ("…" if len(s) > 80 else "")
    return ""


def _sse(payload: dict) -> str:
    """统一构造一条 SSE 事件（与既有事件格式一致）"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ========== 幻觉兜底校验（方案 B） ==========
# 模型可能不调用工具就声称「已创建/已修改/已执行」——必须技术兜底。
# 判定规则：回复同时含【完成态动作】+【文件语境】，且本轮未调用任何工具 → 判定编造。
_FABRICATION_ACTIONS: tuple[str, ...] = (
    "已创建", "已生成", "已写入", "已保存", "已建立", "已新建", "已上传", "已下载",
    "已修改", "已更新", "已删除", "已执行", "已运行", "已重命名",
    "已搜索", "已查找", "已列出", "已读取", "已查看", "已遍历",
    "创建成功", "保存成功", "执行成功", "写入成功", "上传成功",
    "文件不存在", "没有找到", "找不到该文件", "目录为空", "结果为空", "文件为空",
)
_FILE_CONTEXT_WORDS: tuple[str, ...] = (
    "文件", "目录", "文件夹", "脚本", "路径", "沙箱", "命令",
    ".py", ".txt", ".xlsx", ".csv", ".json", ".sh", ".md",
)


def _looks_like_fabricated_action(text: str) -> bool:
    """判断回复是否在声称「完成了文件/命令操作」

    双重条件（降低对正常业务问答的误伤，如「该产品信息已更新」）：
    - 含完成态动作词
    - 且含文件/命令语境词
    """
    if not text:
        return False
    has_action = any(p in text for p in _FABRICATION_ACTIONS)
    has_file_ctx = any(w in text for w in _FILE_CONTEXT_WORDS)
    return has_action and has_file_ctx


def _artifact_block(session_id: str, artifacts: list, skipped: int = 0) -> str:
    """生成回复末尾的「产出文件」Markdown 块（前端已支持 markdown 链接渲染）

    智能体把交付物写进了沙箱，用户点不到 /sandbox/... 那种路径；
    这里给出宿主机的链接：浏览器能打开的给「打开 + 下载」，
    其余（xlsx/docx/zip/tar.gz…）只给下载链接。

    skipped：本轮超过「每轮最多 5 个」上限、没回传的数量（文件仍在沙箱里，
    用户可在「我的文件」里找到），明确告知而不是静默少给几个链接。
    """
    from urllib.parse import quote

    from src.agent.backends.sandbox import inline_displayable

    lines: list[str] = ["\n\n---\n", "📎 **产出文件**（点文件名即可打开或下载）"]
    for a in artifacts:
        name: str = str(a.get("name", ""))
        if not name:
            continue
        url: str = f"/open-agent-harness/api/artifacts/{session_id}/{quote(name, safe='')}"
        kb: float = float(a.get("size", 0) or 0) / 1024
        if kb < 1:
            size_text = f"{int(a.get('size', 0) or 0)} B"
        elif kb >= 1024:
            size_text = f"{kb / 1024:.1f} MB"
        else:
            size_text = f"{kb:.1f} KB"
        if inline_displayable(name):
            lines.append(f"- [{name}]({url})（{size_text}） · [下载]({url}?download=1)")
        else:
            lines.append(f"- [{name}]({url}?download=1)（{size_text}，点击即下载）")
    if skipped > 0:
        lines.append(
            f"\n（另有 **{skipped}** 个文件本轮未回传：每轮最多展示 5 个、单文件上限 100MB；"
            f"文件仍在你的沙箱里，可在「我的文件」里查看和下载。）"
        )
    return "\n".join(lines)


def _safe_error_str(e: Exception) -> str:
    """给用户看的错误文案：**一律友好提示**（详细错误只进日志）

    之前非生产环境直接返回 str(e)，结果把 SQLAlchemy 的 "Lost connection to MySQL…"
    原样贴到对话里（用户看不懂，还暴露了库表结构）。现在只记日志、只回人话。
    """
    try:
        log.error(f"对话处理异常: {type(e).__name__}: {e}", exc_info=True)
    except Exception:
        pass
    return '抱歉，本轮处理出了点问题（已记录日志）。可以重试一次，或换个说法；如果反复出现请联系管理员。'


# ========== 上下文提取（解决模型多轮遗忘关键参数的问题） ==========

_CONTEXT_END_MARK: str = "【对话历史结束】"


def _strip_injected_context(text: str) -> str:
    """还原用户真实输入：剥离注入的账号前缀与历史块

    为什么需要：每轮发给模型的 user 消息形如
        [当前用户域账号: xxx]

        【对话历史】
        第1轮: ...
        【对话历史结束】

        <用户真实输入>

    这条消息同时也会被写进对话历史。下一轮提取历史时若不剥离，
    就会「历史里嵌历史」逐轮递归嵌套，导致上下文膨胀、模型困惑
    （实测曾出现模型思考 47 秒后零输出）。
    """
    t = (text or "").strip()
    if not t:
        return ""
    # 新格式：有明确结束标记 → 取标记之后的内容（用户真实输入）
    if _CONTEXT_END_MARK in t:
        return t.split(_CONTEXT_END_MARK, 1)[1].strip()
    # 去掉账号前缀
    if t.startswith("[当前用户域账号:"):
        idx = t.find("]")
        if idx != -1:
            t = t[idx + 1:].strip()
    # 旧格式（无结束标记但含历史块）→ 无法可靠还原，返回空由调用方跳过
    if t.startswith("【对话历史】"):
        return ""
    return t


async def _build_context_prefix(agent: CompiledStateGraph, config: dict[str, Any],
                          session_id: str = "", username: str = "") -> str:
    """构建每轮注入的"补充上下文"前缀。两种内容，**互不重复**：

    ① 会话摘要（`chat_sessions.summary`）—— 上下文治理压掉的更早对话要点，很短，每轮都带
    ② 【对话历史】文本块 —— **只在 agent state 里没有历史时**才拼（旧会话迁移 / 兜底）

    为什么②要加限制：正常对话的历史本来就在 state 里（模型能看到），再把同一份历史
    拼成文本塞进 user 消息 = **同一份内容发两遍**，token 近双倍（治理前就是这个状态）。
    """
    # ① 会话摘要（很短，与 state 内容不重叠）
    summary: str = ""
    if session_id:
        try:
            summary = await SessionDao.get_summary(session_id, username)
        except Exception as e:
            log.warning(f"读取会话摘要失败（忽略）: {e}")

    # 摘要里可能残留"用户要求用表格"这类旧偏好：用户改/删偏好后它会继续生效。
    # 这里按行过滤掉含偏好/格式要求的行，保证偏好只有"本轮注入"这一个来源。
    if summary.strip():
        _bad = ("偏好", "记住", "以后都", "一律用", "都用表格", "回答用表格", "金额统一", "用万元")
        summary = "\n".join(
            ln for ln in summary.split("\n") if ln.strip() and not any(k in ln for k in _bad)
        )
    summary_block: str = f"【更早对话的摘要】\n{summary}\n\n" if summary.strip() else ""

    # ② state 里已有历史 → 只带摘要，不再拼【对话历史】
    messages: list[Any] = []
    try:
        state = await agent.aget_state(config)
        if state and state.values:
            messages = list(state.values.get("messages", []))
    except Exception:
        pass

    if messages:
        if summary_block:
            log.info(f"上下文前缀: 会话摘要 {len(summary)} 字符（state 已有 {len(messages)} 条历史，不再重复拼装）")
        return summary_block

    # ③ state 为空：旧会话迁移 / checkpointer 无记录的兜底，从 MySQL 取最近几轮
    if session_id:
        try:
            # 注意：必须用「前端会话号 + 用户名」查（内部会解析成主键），
            # 直接拿前端 session_id 比 chat_messages.session_id 永远查不到
            recent_msgs = await SessionDao.get_recent_messages(session_id, username, limit=12)
            if recent_msgs:
                for m in recent_msgs:
                    if m.role == 'user':
                        messages.append(HumanMessage(content=m.content or ''))
                    elif m.role == 'agent':
                        messages.append(AIMessage(content=m.content or ''))
                log.info(f"上下文提取: 从 MySQL 直接加载, messages={len(messages)}")
        except Exception:
            pass

    if not messages:
        return summary_block

    # 提取 user/assistant 成对对话轮次（忽略中间的 tool_calls / tool_result）
    rounds: list[tuple[str, str]] = []
    pending_user: str | None = None
    for m in messages:
        if isinstance(m, HumanMessage):
            # 剥离注入的账号前缀/历史块，避免跨轮次递归嵌套
            content = _strip_injected_context(getattr(m, 'content', '') or '')
            if content:
                pending_user = content
        elif isinstance(m, AIMessage):
            content = (getattr(m, 'content', '') or '').strip()
            if content and pending_user is not None:
                rounds.append((pending_user, content))
                pending_user = None

    if pending_user is not None:
        rounds.append((pending_user, "(无回复)"))

    if not rounds:
        return summary_block

    # 只取最近 6 轮
    rounds = rounds[-6:]

    lines = ["【对话历史】"]
    for i, (user_msg, ai_msg) in enumerate(rounds, 1):
        user_short = user_msg[:200] + ("…" if len(user_msg) > 200 else "")
        ai_short = ai_msg[:200] + ("…" if len(ai_msg) > 200 else "")
        lines.append(f"第{i}轮: 用户「{user_short}」→ AI「{ai_short}」")
    # 结束标记：下一轮提取历史时据此剥离本块，避免「历史嵌历史」递归嵌套
    lines.append(_CONTEXT_END_MARK)

    prefix = summary_block + "\n".join(lines) + "\n\n"
    log.info(f"上下文前缀: {prefix[:500]}")
    return prefix


# ========== 消息清理 ==========

def _sanitize_tool_messages(messages: list[Any]) -> list[Any]:
    """清理孤立的 ToolMessage：DeepSeek API 要求 tool 角色消息必须有对应的
    前置 assistant 消息携带 tool_calls。summarization 中间件截断消息时可能
    破坏这个配对关系，导致 400 错误。此函数移除所有找不到配对 AIMessage 的 ToolMessage。
    """
    from langchain_core.messages import AIMessage, ToolMessage

    # 第一遍：收集所有 AIMessage 中声明的 tool_call_id（即有效的 tool_calls）
    valid_tool_call_ids: set[str] = set()
    for m in messages:
        if isinstance(m, AIMessage):
            tool_calls = getattr(m, 'tool_calls', None)
            if tool_calls:
                for tc in tool_calls:
                    tc_id = tc.get('id', '') if isinstance(tc, dict) else getattr(tc, 'id', '')
                    if tc_id:
                        valid_tool_call_ids.add(tc_id)

    # 第二遍：过滤，移除孤立的 ToolMessage
    cleaned: list[Any] = []
    removed_count = 0
    for m in messages:
        if isinstance(m, ToolMessage):
            tc_id = getattr(m, 'tool_call_id', '') or ''
            if tc_id and tc_id not in valid_tool_call_ids:
                removed_count += 1
                continue
        cleaned.append(m)

    if removed_count > 0:
        log.info(f"消息清理: 移除了 {removed_count} 条孤立的 ToolMessage")
    return cleaned


async def _sanitize_existing_state(agent: CompiledStateGraph, config: dict[str, Any]) -> None:
    """清理 agent 现有 state 中孤立的 ToolMessage"""
    try:
        state = await agent.aget_state(config)
        if state is None or not state.values:
            return
        messages = state.values.get("messages", [])
        if not messages:
            return
        cleaned = _sanitize_tool_messages(list(messages))
        if len(cleaned) != len(messages):
            await agent.aupdate_state(config, {"messages": cleaned})
            log.info(f"已清理 state 中孤立的 ToolMessage: {len(messages)} → {len(cleaned)}")
    except Exception as e:
        log.warning(f"清理现有 state 失败: {e}")


# ========== 上下文治理 ==========

async def _govern_context(agent: CompiledStateGraph, config: dict[str, Any],
                    session_id: str = "", username: str = "") -> None:
    """上下文治理：压工具输出 → 超预算则滚动摘要 → 更新 state。

    替代原来的「超过 200 条从最老整段丢弃」：现在**丢之前一定先交接**
    （压成摘要存库，下一轮作为 `[更早对话的摘要]` 前缀带上）。

    顺序（每步都失败安全）：
    ① 清孤立 ToolMessage（配对检查，避免 DeepSeek 400）
    ② 压较早轮次的工具输出（只改文本、不删消息）
    ③ 仍超预算 → 按【完整轮次】切出最老一段做摘要；摘要失败则**宁可不裁**
       （只有超过 2 倍预算且摘要不可用时才退回尾部硬切，并打 ERROR）
    """
    try:
        from src.chat.context import (
            KEEP_RECENT_ROUNDS,
            SUMMARY_MAX_CHARS,
            compress_tool_outputs,
            split_oldest_rounds,
            summarize,
            total_chars,
        )

        budget: int = max(2000, int(getattr(cfg, "contextCharBudget", 60000) or 60000))

        state = await agent.aget_state(config)
        if state is None or not state.values:
            return
        messages: list[Any] = list(state.values.get("messages", []))
        if not messages:
            return

        before_chars: int = total_chars(messages)
        before_len: int = len(messages)
        changed: bool = False

        # ① 孤立 ToolMessage（配对关系必须成立，否则模型 API 400）
        cleaned = _sanitize_tool_messages(messages)
        if len(cleaned) != len(messages):
            log.info(f"清理孤立 ToolMessage: {len(messages)} → {len(cleaned)} 条")
            messages, changed = cleaned, True

        # ② 压较早轮次的工具输出（最近 KEEP_RECENT_ROUNDS 轮保持完整）
        compressed, saved = compress_tool_outputs(messages, keep_rounds=KEEP_RECENT_ROUNDS)
        if saved > 0:
            messages, changed = compressed, True
            log.info(f"工具输出已压缩: 省 {saved} 字符 → 当前 {total_chars(messages)} 字符")

        # ③ 仍超预算 → 滚动摘要
        chars_now: int = total_chars(messages)
        if chars_now > budget:
            old, keep = split_oldest_rounds(messages, budget, keep_rounds=KEEP_RECENT_ROUNDS)
            if old:
                try:
                    from src.agent.main_agent import get_model

                    prev: str = await SessionDao.get_summary(session_id, username) if session_id else ""
                    new_summary: str = await summarize(get_model(), old, prev_summary=prev)
                    if session_id:
                        await SessionDao.save_summary(session_id, new_summary[:SUMMARY_MAX_CHARS], username)
                    log.info(
                        f"上下文摘要完成: 旧消息 {len(old)} 条（{total_chars(old)} 字符）→ "
                        f"摘要 {len(new_summary)} 字符；保留 {len(keep)} 条"
                    )
                    messages, changed = keep, True
                except Exception as e:
                    # 摘要失败：宁可不裁，也不能无声丢历史
                    log.warning(f"上下文摘要失败，本轮不裁剪（宁可超预算也不丢历史）: {e}")
                    if chars_now > budget * 2:
                        keep_n: int = max(20, len(messages) // 2)
                        messages = messages[-keep_n:]
                        changed = True
                        log.error(
                            f"上下文超过 2 倍预算且摘要不可用，退回尾部硬切: "
                            f"{before_len} → {keep_n} 条（会丢早期上下文）"
                        )

        if changed:
            await agent.aupdate_state(config, {"messages": messages})
            log.info(
                f"上下文治理: {before_len} 条/{before_chars} 字符 → "
                f"{len(messages)} 条/{total_chars(messages)} 字符（预算 {budget}）"
            )
    except Exception as e:
        log.warning(f"上下文治理失败（不影响本轮对话）: {e}")


# ========== Excel 解析 ==========

def _parse_excel_file(file_path: str) -> str:
    """使用 pandas 解析 Excel 文件，返回 JSON 字符串"""
    import numpy as np
    import pandas as pd

    try:
        raw: pd.DataFrame = pd.read_excel(file_path, engine='calamine', header=None)
    except Exception as e:
        raise RuntimeError(f"读取 Excel 文件失败: {e}")

    columns: list[str] = [f"列{i+1}" for i in range(raw.shape[1])]
    rows: list[dict[str, Any]] = []
    for _, row in raw.iterrows():
        r: dict[str, Any] = {}
        for i, v in enumerate(row):
            if pd.notna(v):
                val = v.item() if isinstance(v, np.generic) else v
                if isinstance(val, float) and val == int(val):
                    val = int(val)
                r[columns[i]] = val
        if r:
            rows.append(r)

    result: dict[str, Any] = {"columns": columns, "row_count": len(rows), "data": rows}
    # default=str：Excel 里的日期列是 pandas.Timestamp，不是 JSON 原生类型，
    # 不加这个参数会直接抛 "Object of type Timestamp is not JSON serializable"（实测踩过）
    return json.dumps(result, ensure_ascii=False, default=str)


# ========== 流式主流程 ==========

async def _extract_pending_interrupt(agent: CompiledStateGraph, config: dict[str, Any]) -> dict | None:
    """从 checkpointer 中提取 pending 的 ask_user interrupt 请求。

    ask_user 工具内部调用 interrupt() 后，GraphInterrupt 会把值持久化到
    checkpointer（INTERRUPT 通道）。流式结束后，遍历 state.tasks 里的
    interrupts，找到 value 形如 {"question":..., "options":[...]} 的那个。

    返回 interrupt value dict，无 pending interrupt 时返回 None。
    """
    try:
        state = await agent.aget_state(config)
        if state is None:
            return None
        for task in getattr(state, "tasks", ()) or ():
            for it in getattr(task, "interrupts", ()) or ():
                value = getattr(it, "value", None)
                if isinstance(value, dict) and isinstance(value.get("options"), list) and value.get("options"):
                    return value
    except Exception as e:
        log.warning(f"检测 pending interrupt 失败: {e}")
    return None


# ========== 沙箱文件提示：解决"模型不知道会话里有没有附件" ==========
_SANDBOX_FILES_CACHE: dict = {}
# 最近一次“往沙箱写了文件”的时间（上传后打点）——上传当轮必须告诉模型有哪些文件
_SANDBOX_FILES_FRESH: dict = {}
# 原来 15 秒：用户看一眼回答再打字（通常 20~60 秒）就必定过期，
# 每轮白付一次沙箱往返（实测 3.5 秒）。改成 60 秒，并在"我们自己往沙箱写文件"的
# 入口主动失效（见 invalidate_sandbox_files_hint），正确性不受影响。
_SANDBOX_FILES_TTL: float = 180.0
# 上传后多长时间内强制注入清单（否则用户下一轮说“处理一下”模型不知道有文件）
_FRESH_WINDOW: float = 600.0

# 只在可能用到文件时才注入清单：没有这些词就不用，省一次沙箱往返 + 约 100 token
_FILE_WORDS: tuple[str, ...] = (
    "文件", "附件", "上传", "表格", "文档", "图片", "图纸", "压缩包", "目录",
    "excel", "xlsx", "xls", "csv", "docx", "doc", "pdf", "ppt", "txt", "zip",
    "报表", "数据", "统计", "汇总", "提取", "解析", "转换", "打印", "导出",
)


def _mentions_files(message: str) -> bool:
    """用户这条消息是否可能涉及沙箱里的文件（决定要不要注入文件清单）"""
    s = (message or "").lower()
    return any(w in s for w in _FILE_WORDS)


def invalidate_sandbox_files_hint(username: str) -> None:
    """沙箱文件变了就清提示缓存（上传后调用；模型自己产出的在 /sandbox/output，不在清单里）"""
    try:
        _SANDBOX_FILES_CACHE.pop(username, None)
        import time as _t

        _SANDBOX_FILES_FRESH[username] = _t.time()
    except Exception:
        pass


async def _sandbox_files_hint(username: str, message: str = "") -> str:
    """列出用户沙箱 /sandbox 根目录的文件，给模型一个"到底有没有附件"的确定事实

    背景（评测 q08a/q08b 行为矛盾）：上传文件落在 /sandbox/<原名>，但只有上传当轮会提示，
    下一轮对话模型完全不知道沙箱里有什么，只能靠猜 → 有附件不用 excel_parse、
    没附件却硬调 excel_parse。这里每轮注入一行事实，消除猜测。
    """
    try:
        import time as _t

        now = _t.time()
        # 只在**可能用到文件**时注入：消息里没提文件、也不是刚上传过 → 不注入
        # （"你好"这种纯聊天不再白跑一次沙箱 ls，也省掉约 100 token 噪声）
        _fresh = _SANDBOX_FILES_FRESH.get(username, 0.0)
        if not _mentions_files(message) and (now - _fresh) > _FRESH_WINDOW:
            return ""
        hit = _SANDBOX_FILES_CACHE.get(username)
        if hit and now - hit[0] < _SANDBOX_FILES_TTL:
            names = hit[1]
        else:
            from src.agent.backends.sandbox import sandbox_manager

            if not getattr(sandbox_manager, "enabled", False):
                return ""
            res = await sandbox_manager.aexec(
                username,
                # 根目录 + upload/（上传落点早改成 /sandbox/upload/ 了，
                # 只看根目录会永远得到"空清单"，模型据此认为没有附件）
                "cd /sandbox 2>/dev/null && { ls -1p . 2>/dev/null | grep -v '/$'; "
                "ls -1p upload 2>/dev/null | grep -v '/$' | sed 's#^#upload/#'; } | head -12",
                timeout=8,
            )
            # ExecResult 没有 .ok 字段，用 returncode 判断（==0 才算成功）
            out = (getattr(res, "stdout", "") or "") if getattr(res, "returncode", 1) == 0 else ""
            names = [x.strip() for x in out.splitlines() if x.strip()][:10]
            _SANDBOX_FILES_CACHE[username] = (now, names)

        if not names:
            return ""
        listing = "、".join(names)
        has_excel = any(n.lower().endswith((".xlsx", ".xls")) for n in names)
        tip = (
            "；需要读表格时用 sandbox_shell 跑 pandas"
            if has_excel
            else "；当前没有表格文件"
        )
        return (
            f"[你的沙箱 /sandbox 下现有文件：{listing}]\n"
            f"（用户提到\"这个 Excel/附件/刚才上传的文件\"时，指的就是上面这些文件，"
            f"用 sandbox_shell 处理{tip}。沙箱里没有的文件不要臆测存在。"
            f"**这只是背景事实：用户只是打招呼、或者聊的事跟文件无关时，不要主动提这些文件，"
            f"也不要主动建议去处理它们。**）"
        )
    except Exception as e:
        log.warning(f"读取沙箱文件列表失败（忽略）: {e}")
        return ""


async def _has_pending_interrupt(agent: CompiledStateGraph, config: dict[str, Any]) -> bool:
    """这条线程是否停在某个 interrupt（人工确认）上

    为什么要问：刷新页面后历史消息里的选项按钮仍可点，但那次早就答过了。再 resume
    一次等于把同一个选择重复提交（白跑一轮；如果后面接的是写操作还会重复执行）。
    `get_state().next` 为空表示图已跑完，没有待确认的东西。
    查询失败时**放行**——宁可多跑一次，也别把正常的恢复挡死。
    """
    try:
        snap = await agent.aget_state(config)
        return bool(getattr(snap, "next", None))
    except Exception as e:
        log.warning(f"查询中断状态失败（放行 resume）: {e}")
        return True


async def process_chat_stream(session_id: str, username: str, message: str,
                        resume_value: str | list[str] | None = None) -> Generator[str, None, None]:
    """对外入口：绑定/还原「当前用户」上下文后，委托给 _process_chat_stream

    ContextVar 以前只 set 不 reset：gthread 复用线程时值会残留到下一轮，
    任何"没先 set 就跑工具"的入口（后台线程、以后新增的定时/恢复任务）都会读到
    上一个用户 —— 写类工具会以别人域账号执行。这里用 token + finally 还原。
    """
    from src.agent.backends.sandbox import reset_sandbox_user, set_sandbox_user

    token = set_sandbox_user(username)
    try:
        async for _chunk in _process_chat_stream(session_id, username, message, resume_value):
            yield _chunk
    except Exception as e:
        # 兜底：建会话/落库/取 agent 等**首次 yield 之前**的异常以前会直接把 SSE 连接掐断，
        # 前端只看到 "network error"。这里统一转成协议内的 error+done。
        log.error(f"对话流未捕获异常: session={session_id}, user={username}, "
                  f"{type(e).__name__}: {e}", exc_info=True)
        yield f"data: {json.dumps({'error': _safe_error_str(e)}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"
    finally:
        reset_sandbox_user(token)


async def _process_chat_stream(session_id: str, username: str, message: str,
                         resume_value: str | list[str] | None = None) -> Generator[str, None, None]:
    """流式处理聊天，通过 SSE 逐块返回 LLM 生成内容。

    resume_value 非空时，表示这是用户点击选项后的恢复请求：
    用 Command(resume=...) 继续之前被 ask_user interrupt 暂停的执行。
    """
    # ① 确保会话存在
    _turn_t0: float = time.time()   # 指标：本轮耗时起点
    from src.infra.Timing import Marks

    _mk = Marks(f"session={session_id[:12]}")
    chat_session = await SessionDao.get_or_create_session(session_id, username)
    _mk.mark("① 会话 get_or_create")
    log.info(f"会话(stream): <{session_id}>, 用户: <{username}>, resume={bool(resume_value)}")
    log.info(f"用户输入: {message}")

    # 当前用户已由 process_chat_stream 绑定到 ContextVar（见函数开头）

    # ② 立即持久化用户消息（不等 agent 返回）
    # resume 请求时 message 通常为空，此时用 resume_value 生成可读文本入库，
    # 确保用户的选择（如「3: 提利率，二级拨档」）刷新后能出现在历史里。
    # ── 轨迹：用户输入（放在所有分支之前，任何路径都记）──
    try:
        from src.infra import TraceService as _TS0

        if message:
            # 注意：process_chat_stream 里没有 state（那是 _stream_once 的形参）
            _TS0.record(username=username, session_id=session_id, kind="user",
                        name="用户输入", args="", result=str(message), status="ok",
                        turn_no=0)
    except Exception as _te:
        log.warning(f"轨迹记录用户输入失败（忽略）: {_te}")

    user_msg_content: str = message.strip() if message else ""
    if resume_value is not None and not user_msg_content:
        if isinstance(resume_value, list):
            user_msg_content = "、".join(str(x) for x in resume_value)
        else:
            user_msg_content = str(resume_value)
    if user_msg_content:
        # 独立 session（原因见 _finish 处注释）：流式写入不与请求级 session 共用
        async with session_scope():
            await MessageDao.create_message(chat_session.id, "user", user_msg_content)
    _mk.mark("② 用户消息入库")

    try:
        # ③ 获取 agent
        agent: CompiledStateGraph = await create_agent(session_id, username)
        _mk.mark("③ 构建 agent（含缓存命中/构建）")

        # ③ thread_id 隔离（checkpointer 持久化，历史由 PostgresSaver 自动恢复）
        config: dict[str, Any] = {"configurable": {"thread_id": f"{session_id}_{username}"}}

        if resume_value is not None:
            # 恢复路径：继续被 interrupt 暂停的执行，不重新构建输入。
            # 但**必须先确认这条线程真的停在人工确认点**：刷新页面后历史消息里的
            # 选项按钮是可点的，那时早就答过了；不校验就等于把同一个选择再喂一遍
            # （白跑一轮，若后面的工具是写操作还会重复执行）。
            if not await _has_pending_interrupt(agent, config):
                _done_msg: str = "这个选项已经处理过了（当前没有待确认的选项），请重新发起请求。"
                log.warning(f"忽略无效的 resume：线程无待确认中断 | session={session_id}, user={username}")
                yield f"data: {json.dumps({'error': _done_msg}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n"
                return
            input_payload = Command(resume=resume_value)
            log.info(f"=== 恢复执行(resume) | resume_value={resume_value} ===")
        else:
            # 常规路径：构建新消息输入
            # 提取上下文前缀，防止模型多轮遗忘关键参数
            context_prefix: str = await _build_context_prefix(agent, config, session_id, username)
            _mk.mark("④ 上下文前缀")

            # 轨迹：本轮注入的上下文（系统提示 + 偏好 + 知识 + 文件清单的合成）
            if context_prefix:
                try:
                    from src.infra import TraceService as _TS1

                    _TS1.record(username=username, session_id=session_id, kind="context",
                                name="上下文注入", args="", result=context_prefix, status="ok",
                                turn_no=0)
                except Exception as _te:
                    log.warning(f"轨迹记录上下文失败（忽略）: {_te}")

            # 构建消息（域账号 + 上下文 + 用户消息）
            agent_message: str = f"[当前用户域账号: {username}]\n\n{context_prefix}\n\n{message}" if context_prefix else f"[当前用户域账号: {username}]\n\n{message}"

            # 注：偏好与沙箱文件清单直接拼进用户消息（实测可用）。
            # 曾尝试用 wrap_model_call 钩子做"仅注入本次调用"，但 deepagents 的 agent
            # 不触发该钩子（日志证实），方案不可行，已回退。
            # 历史消息里旧偏好仍会生效的问题，用"版本号 + 只认最新"约束解决。
            _injected: list[str] = []      # 轨迹：本轮注入了哪些上下文
            _sb_hint: str = await _sandbox_files_hint(username, message)
            _mk.mark("⑤ 沙箱文件清单")
            if _sb_hint:
                agent_message = f"{agent_message}\n\n{_sb_hint}"
                _injected.append('[沙箱文件清单]' + chr(10) + _sb_hint)
            try:
                from src.agent.memory.user_prefs import render_hint as _pref_hint

                _ph: str = _pref_hint(username)
                _mk.mark("⑥ 用户偏好")
                if _ph:
                    agent_message = f"{agent_message}\n\n{_ph}"
                    _injected.append('[用户长期偏好]' + chr(10) + _ph)
            except Exception as _e:
                log.warning(f"注入用户偏好失败（忽略）: {_e}")

            # 已知业务知识（关键词召回，≤3 条 / ≤800 字符）
            try:
                from src.agent.memory.knowledge import render_hint as _know_hint

                _kh = _know_hint(username, message)
                if _kh:
                    agent_message = f"{agent_message}\n\n{_kh}"
                    _injected.append('[业务知识]' + chr(10) + _kh)
            except Exception as _e:
                log.warning(f"注入业务知识失败（忽略）: {_e}")

            # —— 用户输入侧防护（检测 + 警示，不硬拦）——
            try:
                from src.chat.input_guard import guard_prefix

                _guard, _scan = guard_prefix(message)
                if _scan.get("injection"):
                    log.warning(f"用户输入命中注入特征: user={username}, hits={_scan['hits'][:3]}")
                    agent_message = f"{_guard}\n{agent_message}"
                if _scan.get("sensitive"):
                    log.info(f"用户输入含敏感信息（仅记录）: user={username}, kinds={_scan['sensitive']}")
                if _scan.get("long"):
                    log.info(f"用户输入较长: user={username}, len={_scan['length']}")
            except Exception as _e:
                log.warning(f"输入侧防护异常（忽略）: {_e}")

            # 轨迹：本轮注入了哪些上下文（知识/偏好/文件清单），排查用
            if _injected:
                try:
                    from src.infra import TraceService as _TS2

                    _TS2.record(username=username, session_id=session_id, kind="context",
                                name="上下文注入", args="",
                                result=(chr(10)+chr(10)).join(_injected), status="ok", turn_no=0)
                except Exception as _te:
                    log.warning(f"轨迹记录上下文失败（忽略）: {_te}")

            # 构建输入：清理现有 state 中孤立的 ToolMessage，再追加新消息
            from langchain_core.messages import HumanMessage as _HM2
            input_payload = {"messages": [_HM2(content=agent_message)]}
            # 清理现有 state 中孤立的 ToolMessage（summarization 中间件可能破坏配对关系）
            await _sanitize_existing_state(agent, config)
            state_before = await agent.aget_state(config)
            history_count = len(state_before.values.get("messages", [])) if (state_before and state_before.values) else 0
            log.info(f"=== 发送给模型(stream) | 历史消息数={history_count}, 新增消息({len(agent_message)}字符) ===\n{agent_message}")

        # ④ 同步流式执行，stream_mode="messages" 逐 token 产出
        # ReAct agent 循环中，有 tool_calls 的消息是"思考+行动"，无 tool_calls 的才是最终回复。
        # 检测到 tool_call_chunks 即标记跳过当前消息，其余 content 直接流式输出，不再整段缓冲。
        # 流式消费的状态容器（抽成 _stream_once 以支持「空回复自动重试」）
        acc: dict = {"text": "", "tools": False, "chunks": 0, "reasoning": 0}

        def _extract_tool_content(msg) -> str:
            """从 ToolMessage/ToolMessageChunk 中提取文本内容，兼容 string 和 list 格式"""
            raw = getattr(msg, 'content', '') or ''
            if isinstance(raw, str):
                return raw
            if isinstance(raw, list):
                parts = []
                for block in raw:
                    if isinstance(block, dict) and block.get('type') == 'text':
                        parts.append(str(block.get('text', '')))
                    elif isinstance(block, str):
                        parts.append(block)
                return '\n'.join(parts)
            return str(raw)

        def _try_emit_navigation(tool_content: str):
            """尝试从工具输出中提取导航指令（含 url 的 JSON）并 yield SSE 事件"""
            if not tool_content or not tool_content.strip():
                return
            stripped = tool_content.lstrip()
            # 注入防护中间件会给结果加 <tool_result ...> 包裹和 ⚠️ 前缀，
            # 这里跳过包裹，直接从第一个 JSON 对象开始解析（否则导航永远不触发）
            brace = stripped.find('{')
            if brace < 0:
                return
            try:
                parsed, _ = json.JSONDecoder().raw_decode(stripped[brace:])
            except (json.JSONDecodeError, TypeError):
                return
            if not isinstance(parsed, dict) or 'url' not in parsed:
                return
            nav_url = str(parsed.get('url', '')).strip().strip('`').strip()
            nav_name = str(parsed.get('name', ''))
            nav_message = str(parsed.get('message', ''))
            if not nav_url:
                return
            if not _is_safe_nav_url(nav_url):
                log.warning(f"忽略非 http(s) 的导航 URL（防同源脚本执行）: {nav_url[:120]!r}")
                return
            log.info(f"检测到导航指令(tool): url={nav_url}, name={nav_name}")
            nav_event = {'url': nav_url}
            if nav_name:
                nav_event['name'] = nav_name
            yield f"data: {json.dumps(nav_event, ensure_ascii=False)}\n\n"
            if nav_message:
                yield f"data: {json.dumps({'content': nav_message}, ensure_ascii=False)}\n\n"

        async def _stream_once(state: dict) -> AsyncGenerator[str, None]:
            """消费一轮 agent 流：yield SSE 事件，统计写回 state

            抽成独立函数是为了支持「空回复自动重试」（见下方 while）。
            每轮内部状态（current_msg_id / skip_message）各自独立，重试时自动重置。
            """
            current_msg_id: str | None = None
            skip_message: bool = False
            _ttft_done: bool = False

            async for chunk in agent.astream(
                input_payload,
                config=config,
                stream_mode="messages"
            ):
                # chunk 格式: (AIMessageChunk/ToolMessageChunk, metadata)
                if not (isinstance(chunk, tuple) and len(chunk) >= 1):
                    continue
                if not _ttft_done:
                    _ttft_done = True
                    _mk.mark("⑦ 首字（模型第一片）")
                state["chunks"] = state.get("chunks", 0) + 1

                msg = chunk[0]
                msg_id: str | None = getattr(msg, 'id', None)

                # 指标：模型返回的 token 用量（DeepSeek 通常在最后一片带 usage_metadata）
                _usage = getattr(msg, 'usage_metadata', None)
                if isinstance(_usage, dict):
                    try:
                        if _usage.get('input_tokens'):
                            state["tokens_in"] = int(_usage['input_tokens'])
                        if _usage.get('output_tokens'):
                            state["tokens_out"] = int(_usage['output_tokens'])
                    except (TypeError, ValueError):
                        pass

                # ToolMessageChunk（工具执行结果）— 尝试拦截导航指令
                msg_type: str = getattr(msg, 'type', '') or ''
                if msg_type == 'tool':
                    state["tools"] = True  # 收到工具执行结果 → 本轮确实执行过工具
                    # 过程展示：该步骤执行结束（补上耗时与参数摘要，前端把卡片标记为完成）
                    _tid: str = str(getattr(msg, 'tool_call_id', '') or '')
                    _running: dict = state.setdefault("tool_running", {})
                    if _tid and _tid in _running:
                        _info: dict = _running.pop(_tid)
                        # 技能工具热更新：这一轮若读写过技能目录（tools.json / SKILL.md），
                        # 收尾清 agent 缓存 → 下一轮重建时重新读取技能工具声明（含按需披露）
                        if str(_info.get("name") or "") in ("write_file", "edit_file", "sandbox_shell", "read_file") \
                                and "/sandbox/skills" in str(_info.get("args", "")):
                            state["skills_touched"] = True
                        # 指标：工具报错次数（ToolMessage.status == "error"）
                        if str(getattr(msg, "status", "") or "") == "error":
                            state["tool_errors"] = int(state.get("tool_errors") or 0) + 1
                        _tool_elapsed = round(time.time() - _info.get("t0", time.time()), 3)
                        # trace：记录每个工具 span（名字 / 耗时 / 是否报错）
                        try:
                            acc.setdefault("trace_tools", []).append({
                                "name": str(_info.get("name") or _tid),
                                "elapsed_ms": int(_tool_elapsed * 1000),
                                "status": "error" if str(getattr(msg, "status", "") or "") == "error" else "ok",
                            })
                        except Exception:
                            pass

                        # 审计：只记写 / 执行类工具，以及被拒绝的调用
                        _risk: str = ""
                        _content: str = ""
                        try:
                            from src.infra import AuditService
                            from src.agent.tools.registry import REGISTRY as _REG

                            _tool_name = str(_info.get("name") or _tid)
                            _spec = next((x for x in _REG if x.name == _tool_name), None)
                            _risk = getattr(_spec, "risk", "") if _spec else ""
                            _content = _extract_tool_content(msg) or ""
                            # 去掉注入防护的包裹前缀，审计里保持干净
                            import re as _re2
                            _content = _re2.sub(r"<tool_result[^>]*>|</tool_result>", "", _content).strip()
                            _content = _re2.sub(r"^⚠️[^\n]*\n", "", _content).strip()
                            if "已取消写入" in _content or "参数错误" in _content:
                                _result = "rejected"
                            elif str(getattr(msg, "status", "") or "") == "error":
                                _result = "error"
                            else:
                                _result = "ok"
                            AuditService.record(
                                username=username, tool_name=_tool_name, risk=_risk,
                                params=_info.get("args", ""), result=_result,
                                detail=_content[:500], elapsed_ms=int(_tool_elapsed * 1000),
                                session_id=session_id, trace_id=str(acc.get("trace_id") or ""),
                            )
                        except Exception as _ae:
                            log.warning(f"审计记录失败（忽略）: {_ae}")

                        # 轨迹：每个工具调用都记（参数与结果基本存全文，供排查用）
                        try:
                            from src.infra import TraceService

                            _tr_status = "fail" if str(getattr(msg, "status", "") or "") == "error" else "ok"
                            if "已取消写入" in (_content or "") or "参数错误" in (_content or ""):
                                _tr_status = "denied"
                            await asyncio.to_thread(TraceService.record, 
                                username=username, session_id=session_id, kind="tool",
                                name=str(_info.get("name") or _tid),
                                args=_info.get("args", ""), result=_content,
                                status=_tr_status, elapsed_ms=int(_tool_elapsed * 1000),
                                risk=_risk,
                                turn_no=int(state.get("turn_no") or 0),
                                step_no=int(len(acc.setdefault("trace_tools", []))),
                            )
                        except Exception as _te:
                            log.warning(f"轨迹记录失败（忽略）: {_te}")
                        yield _sse({"step": {
                            "id": _tid,
                            "status": "done",
                            "elapsed": _tool_elapsed,
                            "detail": _tool_detail(str(_info.get("args", ""))),
                        }})
                    for _chunk in _try_emit_navigation(_extract_tool_content(msg)):
                        yield _chunk
                    continue

                # 思考内容（DeepSeek 等模型的 reasoning_content，在 additional_kwargs 里）
                # 既保留在 LangGraph state 中（多轮推理链的连续性），也**实时推给前端展示**。
                # 注意：只推给前端，**绝不写 MySQL** —— 刷新/换设备后思考过程自然消失。
                additional: dict = getattr(msg, 'additional_kwargs', {}) or {}
                reasoning: str = str(
                    additional.get('reasoning_content', '') or additional.get('think', '') or ''
                )
                if reasoning.strip():
                    state["reasoning"] = state.get("reasoning", 0) + len(reasoning)
                    if state["reasoning"] <= _MAX_REASONING_PUSH:
                        yield _sse({"reasoning": reasoning})
                    continue

                # 消息 ID 变化 → 新消息开始，重置跳过标记
                if msg_id and msg_id != current_msg_id:
                    current_msg_id = msg_id
                    skip_message = False

                # 检测 tool_call_chunks：有工具调用的消息是思考步骤，整条跳过
                tool_chunks = getattr(msg, 'tool_call_chunks', None)
                if tool_chunks:
                    skip_message = True
                    state["tools"] = True  # 模型发起了工具调用
                    # 记下工具名：会话收尾时据此判断「这一轮有没有往沙箱里写过东西」，
                    # 只有写过才需要跑技能同步（避免每轮对话都白跑几次 CLI）
                    _names: set = state.setdefault("tool_names", set())
                    _running: dict = state.setdefault("tool_running", {})
                    for _tc in tool_chunks:
                        # 兼容 dict / 对象两种 chunk 形态
                        if isinstance(_tc, dict):
                            _n = str(_tc.get("name") or "")
                            _tid = str(_tc.get("id") or "")
                            _args = _tc.get("args") or ""
                        else:
                            _n = str(getattr(_tc, "name", "") or "")
                            _tid = str(getattr(_tc, "id", "") or "")
                            _args = getattr(_tc, "args", "") or ""
                        if not isinstance(_args, str):
                            try:
                                _args = json.dumps(_args, ensure_ascii=False)
                            except (TypeError, ValueError):
                                _args = ""
                        # 参数是分片到达的：**只有首片带 id，后续分片 id 为空**，
                        # 因此按「最近出现过的那个调用」归集，否则参数摘要永远是空的。
                        if _tid:
                            state["_cur_tid"] = _tid
                            _running.setdefault(_tid, {"t0": time.time(), "args": ""})
                        _cur = str(state.get("_cur_tid") or "")
                        if _cur and _args:
                            _running.setdefault(_cur, {"t0": time.time(), "args": ""})
                            _running[_cur]["args"] = str(_running[_cur].get("args", "")) + _args
                        if _n:
                            _names.add(_n)
                            # 单轮护栏：步数/时长任一超限就收尾（防止一轮跑十几分钟）
                            state["calls"] = int(state.get("calls") or 0) + 1
                            _elapsed: float = time.time() - _turn_t0
                            _max_s: int = int(getattr(cfg, "turnMaxSeconds", 1200) or 1200)
                            _max_n: int = int(getattr(cfg, "turnMaxSteps", 80) or 80)
                            if _elapsed > _max_s or state["calls"] > _max_n:
                                state["budget_hit"] = True
                                log.warning(
                                    f"单轮达到上限已收尾: session={session_id}, user={username}, "
                                    f"步数={state['calls']}, 耗时={int(_elapsed)}s"
                                )
                                _note: str = (
                                    f"\n\n⏱ **本轮已执行 {state['calls']} 步 / {int(_elapsed)} 秒**，"
                                    f"达到单轮上限（{_max_n} 步 / {_max_s} 秒），我先收尾。\n"
                                    f"需要继续的话回一句「继续」，我接着往下做。"
                                )
                                # 必须同时累进 acc["text"]：full_response 是从它取的，
                                # 只 yield 不累加 → 落库/回复里会变成"抱歉，我暂时无法处理…"
                                acc["text"] = (acc.get("text") or "") + _note
                                yield _sse({"content": _note})
                                return
                        # 新出现的工具调用 → 立刻推「进行中」卡片（用户马上看到"正在做什么"）
                        if _n and _tid and not _running[_tid].get("pushed"):
                            _running[_tid]["pushed"] = True
                            _running[_tid]["name"] = _n
                            yield _sse({"step": {
                                "id": _tid,
                                "name": _n,
                                "label": _tool_label(_n),
                                "status": "running",
                            }})
                    continue

                if skip_message:
                    continue

                # 直接流式输出 content，不再缓冲整条消息
                content = getattr(msg, 'content', '') or ''
                if content:
                    state["text"] = state.get("text", "") + content
                    yield f"data: {json.dumps({'content': content}, ensure_ascii=False)}\n\n"

        async for _chunk in _stream_once(acc):
            yield _chunk
        full_response: str = acc.get("text", "")
        tools_used: bool = bool(acc.get("tools"))

        # ===== 空回复自动重试 =====
        # 实测：模型偶发返回空内容（有一次思考 47 秒后零输出），用户会看到
        # 「抱歉，我暂时无法处理这个请求」。这里在「无工具调用」前提下最多重试 2 次。
        _retry: int = 0
        while not full_response.strip() and not tools_used and _retry < 2:
            _retry += 1
            log.warning(
                f"模型返回空内容，重试第 {_retry}/2 次: user={username}, "
                f"session={session_id}, 上轮chunks={acc.get('chunks', 0)}, "
                f"reasoning长度={acc.get('reasoning', 0)}"
            )
            # 重试 = 重新生成：先通知前端清空已展示的思考/步骤，避免两轮内容堆在一起
            yield _sse({"reset": True})
            acc = {"text": "", "tools": False, "chunks": 0, "reasoning": 0}
            async for _chunk in _stream_once(acc):
                yield _chunk
            full_response = acc.get("text", "")
            tools_used = bool(acc.get("tools"))

        log.info(
            f"流式统计: 重试={_retry}次, 内容长度={len(full_response)}, "
            f"chunks={acc.get('chunks', 0)}, reasoning长度={acc.get('reasoning', 0)}, "
            f"工具调用={tools_used}"
        )

        # ===== 幻觉兜底校验（方案 B）=====
        # 声称完成了文件操作，却根本没调用任何工具 → 判定为编造，追加系统提示
        if not tools_used and _looks_like_fabricated_action(full_response):
            log.warning(
                f"【幻觉兜底】检测到疑似编造（声称完成操作但未调用工具）: "
                f"user={username}, 回复片段={full_response.strip()[:150]!r}"
            )
            warn_text = (
                "\n\n---\n"
                "⚠️ **系统校验提示**：以上回复提到的文件/命令操作**并未真正执行**"
                "（本次回答未调用任何执行工具，没有任何文件被创建或修改）。\n\n"
                "如需真正执行，请回复「**请在沙箱中执行**」，我会在你的隔离沙箱里完成。"
            )
            full_response += warn_text
            yield f"data: {json.dumps({'content': warn_text}, ensure_ascii=False)}\n\n"

        # 检查是否有 pending interrupt（ask_user 工具触发的用户选择请求）
        # interrupt 时 agent 暂停执行：发送 options 事件让前端渲染选项按钮。
        # 但会话已经创建、用户消息已入库，且 agent 的 question 文字也应持久化，
        # 否则刷新页面后 AI 回复丢失、新会话也不进左侧列表。
        pending = await _extract_pending_interrupt(agent, config)
        if pending:
            opt_question = str(pending.get("question", "")).strip()
            opt_options = [str(o).strip() for o in pending.get("options", []) if str(o).strip()]
            opt_event = {'options': {'question': opt_question, 'options': opt_options}}
            yield f"data: {json.dumps(opt_event, ensure_ascii=False)}\n\n"
            log.info(f"检测到 ask_user interrupt: question={opt_question}, options={len(opt_options)}个")

            # 持久化 agent 的提问文字（避免刷新后丢失），并更新会话时间
            # options 以 JSON 存库，刷新后前端可恢复复选框按钮
            agent_reply = (full_response.strip() + "\n" + opt_question).strip() if full_response.strip() else (opt_question or "请选择：")
            options_json: str | None = json.dumps(
                {"question": opt_question, "options": opt_options},
                ensure_ascii=False,
            ) if opt_options else None
            # 独立 session：流式生成器跑在 StreamingResponse 的独立任务里，
            # 与请求级 session 不在同一个 greenlet 上下文；高并发下共用会报
            # `greenlet_spawn has not been called; can't call await_only() here`
            # → 回复没落库（界面上看得到回复，刷新后丢失）。
            async with session_scope():
                await MessageDao.create_message(chat_session.id, "agent", agent_reply, options=options_json)
                await SessionDao.update_session_time(chat_session)

            # 发 done 事件，让前端刷新侧边栏会话列表（新会话立即出现）
            await _finish_turn(acc, username, session_id, _turn_t0)
            yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            return

        # 检查 LLM 响应是否为导航指令 (含 url 的 JSON)
        # 策略：从 LLM 全文响应中提取 {"url": "..."} JSON 对象（可能嵌在文字中或代码块中）
        nav_url = None
        nav_message = None
        nav_name = None
        _nav_text = full_response.strip()

        # 方法 1: markdown 代码块包裹
        _json_match = _re.search(r'```(?:json)?\s*\n?([\s\S]*?)\n?```', _nav_text)
        # 方法 2: 文字中嵌入的 JSON（查找含 "url" 的 {...} 对象）
        if not _json_match:
            _json_match = _re.search(r'\{[^{}]*"url"\s*:\s*"[^"]*"[^{}]*\}', _nav_text)
        if _json_match:
            _nav_text = _json_match.group(0).strip()

        try:
            resp_json = json.loads(_nav_text)
            if isinstance(resp_json, dict) and 'url' in resp_json:
                _cand = str(resp_json['url']).strip().strip('`').strip()
                if not _is_safe_nav_url(_cand):
                    # 模型/工具输出可能被注入成 javascript:/data: 链接，前端会直接打开
                    log.warning(f"忽略非 http(s) 的导航 URL（防同源脚本执行）: {_cand[:120]!r}")
                else:
                    nav_url = _cand
                    nav_message = resp_json.get('message', '')
                    nav_name = resp_json.get('name', '')
                    log.info(f"检测到导航指令: url={nav_url}, name={nav_name}")
        except (json.JSONDecodeError, TypeError):
            pass

        if nav_url:
            # 将 url 作为 SSE 事件发送给前端
            nav_event = {'url': nav_url}
            if nav_name:
                nav_event['name'] = nav_name
            yield f"data: {json.dumps(nav_event, ensure_ascii=False)}\n\n"
            log.info(f"导航指令已发送 url={nav_url}, name={nav_name}")
            if nav_message:
                full_response = nav_message
                # 重新发送 message 覆盖之前流式输出的原始 JSON
                yield f"data: {json.dumps({'content': nav_message}, ensure_ascii=False)}\n\n"

        # ===== 正文已结束：先告诉前端「打字结束」=====
        # 模型最后一个字到这里之间，服务端还要做收尾（产物回传 / 落库 / 轨迹 / 指标），
        # 有时要十几秒。前端如果等 done 才撤掉转圈，用户就会看到「回答完了还在转」。
        # 所以这里单独发一个 typing=false，语义是「正文没了，别再转圈」，连接继续留着。
        try:
            yield f"data: {json.dumps({'typing': False}, ensure_ascii=False)}\n\n"
        except Exception:
            pass

        # ===== 产出文件回传（沙箱 → 宿主机 → 用户可打开/下载）=====
        # 每轮扫一次（只在内容变化时才真正下载），把用户点不到的沙箱文件变成
        # 可点链接；已经生成过的报告在后续任意一轮也会被带上。
        # 上限口径：单文件 100MB、每轮最多回传 5 个（超出不下载、不删除，只提示数量）。
        try:
            from src.agent.backends.sandbox import sandbox_manager

            _arts: list = []
            _skipped: int = 0
            # 只有**这一轮真的调过工具**才扫沙箱：纯聊天（"你好"）不可能产出文件，
            # 却照样跑一遍 find（5~10 秒），done 因此被推迟，前端就一直转圈。
            _used_tools: bool = bool(acc.get("trace_tools") or acc.get("tools"))
            if sandbox_manager.enabled and _used_tools:
                _arts, _skipped = await sandbox_manager.acollect_artifacts(username, session_id)
            # 只展示「这一轮新产出/有变化」的，或「回复里确实提到了她」的文件，
            # 避免每轮都把历史文件重复贴一遍
            _show: list = [
                a for a in _arts
                if a.get("changed") or (a.get("name") and a["name"] in full_response)
            ]
            if _show or _skipped:
                _block: str = _artifact_block(session_id, _show, skipped=_skipped)
                full_response += _block
                yield f"data: {json.dumps({'content': _block}, ensure_ascii=False)}\n\n"
        except Exception as e:
            log.warning(f"产出文件回传失败（不影响回复）: user={username}, err={e}")


        # 发送结束信号
        if not full_response:
            fallback = "抱歉，我暂时无法处理这个请求，请稍后重试。"
            full_response = fallback
            yield f"data: {json.dumps({'content': fallback}, ensure_ascii=False)}\n\n"

        # ⑦ 持久化 agent 响应 —— **必须放在 done 之前**
        # 客户端收到 done 就可能断流，生成器会在 done 处被关闭；写在 done 之后的
        # 代码在「点停止 / 断网 / worker 超时被杀」时永不执行 → 整轮回复永久丢失
        # （同步给用户的 _finish_turn 注释已经写明这个坑）。落库失败不影响本轮。
        try:
            # 独立 session：流式生成器跑在 StreamingResponse 的独立任务里，
            # 与请求级 session 不在同一个 greenlet 上下文；高并发下共用会报
            # `greenlet_spawn has not been called; can't call await_only() here`
            # → 回复没落库（界面上看得到回复，刷新后丢失）。
            async with session_scope():
                await MessageDao.create_message(chat_session.id, "agent", full_response)
                await SessionDao.update_session_time(chat_session)
        except Exception as e:
            log.error(f"回复落库失败（回复已发出，不影响本轮）: session_id={session_id}, err={e}")

        # 本轮指标/trace 只在 _finish_turn 里记一次（这里原来还有一个 record_turn，
        # 每轮被统计了两遍，turns/tokens 全部翻倍）
        # ⑥ 收尾治理挂成后台 task（放在 done 之前：done 之后生成器随时可能被关闭）
        _spawn_post_turn(agent, config, session_id, username, acc)
        await _finish_turn(acc, username, session_id, _turn_t0)
        yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"

        log.info(f"Agent 流式响应完成, 总长度={len(full_response)}")



    except HallucinationLoopError as e:
        log.error(f"Agent 幻觉循环已终止: {e}")
        user_msg = (
            "抱歉，当前对话遇到了技术问题（模型工具调用出现错误）。\n\n"
            "请尝试以下方式：\n"
            "1. 点击「新对话」重新开始\n"
            "2. 换一种方式描述你的需求\n"
            "3. 如果问题持续，请联系管理员"
        )
        yield f"data: {json.dumps({'content': user_msg}, ensure_ascii=False)}\n\n"
        await _finish_turn(acc, username, session_id, _turn_t0)
        yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"
    except Exception as e:
        log.error(f"流式处理失败: {e}", exc_info=True)
        yield f"data: {json.dumps({'error': _safe_error_str(e)}, ensure_ascii=False)}\n\n"


# ========== 图片 OCR（服务端预处理，避免 Agent 幻觉循环） ==========

def _ocr_image(file_path: str) -> str | None:
    """使用视觉模型（DeepSeek）直接对图片做 OCR，返回提取的文字内容
    
    服务端直接调用视觉 API，不经过 Agent，从根本上避免 Agent 的
    工具名幻觉死循环问题。失败时返回 None，由调用方决定降级策略。
    """
    try:
        if not cfg.deepseekApiKey or not cfg.deepseekBaseUrl:
            log.warning("OCR 跳过: deepseekApiKey 或 deepseekBaseUrl 未配置")
            return None

        # base64 编码图片
        ext = os.path.splitext(file_path)[1].lower()
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".gif": "image/gif",
            ".bmp": "image/bmp", ".webp": "image/webp",
        }
        mime = mime_map.get(ext, "image/png")
        with open(file_path, "rb") as f:
            img_data = base64.b64encode(f.read()).decode("utf-8")
        image_url = f"data:{mime};base64,{img_data}"
        log.info(f"OCR 图片已编码: file={os.path.basename(file_path)}, size={len(img_data)} chars")

        # 构建请求（复用对话模型：deepseek-v4-flash 自带图片模态，无需单独视觉模型）
        body = {
            "model": cfg.deepseekModel,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": (
                        "请仔细分析这张图片，提取其中的所有文字内容。\n"
                        "要求：\n"
                        "1. 如果图片中包含表格，请以表格形式呈现\n"
                        "2. 保留原始的文字结构和排版信息\n"
                        "3. 如果有数字、日期等关键信息，请准确提取\n"
                        "4. 用中文回复，直接输出提取的内容，不要添加额外说明"
                    )},
                ],
            }],
            "max_tokens": 4096,
        }

        url = cfg.deepseekBaseUrl.rstrip("/") + "/chat/completions"
        log.info(f"OCR API 请求: url={url}, model={cfg.deepseekModel}")
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg.deepseekApiKey}",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        if "choices" not in result or not result["choices"]:
            log.warning(f"OCR API 返回无 choices: {json.dumps(result, ensure_ascii=False)[:300]}")
            return None

        content = result["choices"][0]["message"]["content"]
        log.info(f"OCR 提取成功: file={os.path.basename(file_path)}, "
                 f"length={len(content)}, model={result.get('model', '?')}")
        return content

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")[:500]
        log.warning(f"OCR API HTTP {e.code}: {error_body}")
        return None
    except urllib.error.URLError as e:
        log.warning(f"OCR 网络错误: {e.reason}")
        return None
    except Exception as e:
        log.warning(f"OCR 处理异常: {type(e).__name__}: {e}")
        return None


def is_image_file(file_path: str) -> bool:
    ext: str = os.path.splitext(file_path)[1].lower()
    return ext in {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}


def is_skill_file(file_path: str, original_name: str = "") -> bool:
    """判断上传的是不是技能包（文件名必须叫 SKILL.md）"""
    name: str = (original_name or os.path.basename(file_path or "")).strip().lower()
    return name == "skill.md" or name.endswith(".skill.md")


async def _handle_skill_upload(session_id: str, username: str, file_path: str,
                         display_name: str) -> Generator[str, None, None]:
    """技能包上传：安全审计 → 装进该用户沙箱的技能目录 → 告知结果

    安装位置：`/sandbox/skills/<技能名>/SKILL.md`
    deepagents 的 SkillsMiddleware 会在下次对话时自动加载该目录下的技能说明，
    因此用户装的技能只对自己生效（文件在本人沙箱内，其他用户看不到）。
    """
    import shlex

    from src.infra.utils.SkillAudit import audit_skill_md, parse_skill_meta

    def _emit(text: str, done: bool = False) -> str:
        payload: dict = {"content": text} if not done else {"done": True, "session_id": session_id}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    # ---------- 读内容 ----------
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content: str = f.read()
    except Exception as e:
        log.warning(f"读取技能包失败: user={username}, err={e}")
        yield _emit(f"❌ 技能包「{display_name}」读取失败，请重新上传。")
        yield _emit("", done=True)
        return

    # ---------- 安全审计 ----------
    passed, blocked, warned = audit_skill_md(content)
    if not passed:
        log.warning(f"技能包审计未通过: user={username}, file={display_name}, 命中={blocked}")
        lines = "\n".join(f"- {b}" for b in blocked)
        yield _emit(
            f"❌ 技能包「{display_name}」**未通过安全审计**，已拒绝安装。\n\n"
            f"命中以下问题：\n{lines}\n\n"
            f"技能说明的内容会被注入系统提示词，因此不允许包含上述指令。"
            f"请修改措辞后重新上传。"
        )
        yield _emit("", done=True)
        return

    # ---------- 解析元信息 ----------
    meta: dict = parse_skill_meta(content, fallback_name=os.path.splitext(display_name)[0])
    skill_name: str = meta.get("name") or "user-skill"

    # ---------- 装进本人沙箱 ----------
    try:
        from src.agent.backends.sandbox import sandbox_manager
        if not sandbox_manager.enabled:
            yield _emit("❌ 沙箱功能未启用，无法安装技能包。")
            yield _emit("", done=True)
            return

        skill_dir: str = f"/sandbox/skills/{skill_name}"
        remote_path: str = f"{skill_dir}/SKILL.md"
        # 先建目录（CLI upload 不会自动创建多级目录）
        await sandbox_manager.aexec(username, f"mkdir -p {shlex.quote(skill_dir)}")

        if not await sandbox_manager.aupload(username, file_path, remote_path):
            yield _emit("❌ 技能包写入你的沙箱失败，请稍后重试。")
            yield _emit("", done=True)
            return
    except Exception as e:
        log.warning(f"技能包安装异常: user={username}, err={e}")
        yield _emit(f"❌ 技能包安装失败：{type(e).__name__}")
        yield _emit("", done=True)
        return

    # ---------- 结果反馈 ----------
    log.info(f"技能包安装成功: user={username}, skill={skill_name}, path={remote_path}")

    # 技能可能声明了自带工具（tools.json）→ 清掉 agent 缓存，让下一轮重新构建时把新工具注册上
    try:
        from src.agent.main_agent import evict_agent_from_cache

        evict_agent_from_cache(session_id, username)
    except Exception as e:
        log.warning(f"清理 agent 缓存失败（新技能的工具将在下一轮对话生效）: {e}")

    desc: str = meta.get("description") or ""
    parts: list[str] = [
        f"✅ 技能包 **{skill_name}** 已安装到你的沙箱：`{remote_path}`",
    ]
    if desc:
        parts.append(f"\n**技能说明**：{desc}")
    parts.append(
        "\n之后当你的问题匹配该技能的适用场景时，助手会按这份说明来处理。"
        "\n（同名技能包重新上传即可覆盖更新。）"
    )
    if warned:
        parts.append("\n\n⚠️ 提示（不影响使用）：")
        parts.extend(f"\n- {w}" for w in warned)

    yield _emit("".join(parts))
    yield _emit("", done=True)


# ========== 文件上传流式处理（图片 + Excel） ==========

async def process_upload_stream(session_id: str, username: str, file_path: str,
                          message: str = "",
                          original_name: str = "") -> Generator[str, None, None]:
    """文件上传流式处理，通过 SSE 实时返回 Agent 处理进度

    图片：服务端直接调视觉模型 OCR，文字传给 agent
    Excel：服务端 pandas 解析后，数据预览传给 agent

    隔离要求（重要）：
    上传的文件会被放进**该用户自己的沙箱**（`/sandbox/<原始文件名>`），
    用户之间互相看不到对方的文件，也不会落到共享目录供他人读取。
    """
    file_name: str = os.path.basename(file_path)
    display_name: str = original_name or file_name

    # 收尾用的上下文：必须**在函数最前面**初始化。
    # 之前定义在 Excel 分支之前，图片 OCR 失败分支提前用了 acc/_turn_t0 →
    # UnboundLocalError，SSE 在 done 之前断流（用户只看到连接断开）。
    acc: dict = {}
    _turn_t0: float = time.time()

    # 把上传文件放进该用户自己的沙箱（隔离：只会进本人的 agent-{username} 沙箱）
    sandbox_path: str = ""
    try:
        from src.agent.backends.sandbox import sandbox_manager
        if sandbox_manager.enabled:
            # 先建目录（CLI upload 不会自动创建多级目录，缺目录会静默失败落到根）
            await sandbox_manager.aexec(username, "mkdir -p /sandbox/upload")
            if await sandbox_manager.aupload(username, file_path, f"/sandbox/upload/{display_name}"):
                sandbox_path = f"/sandbox/upload/{display_name}"
                # 刚传进去的文件必须立刻出现在给模型的清单里（缓存否则要等 TTL）
                invalidate_sandbox_files_hint(username)
                log.info(f"上传文件已存入用户沙箱: user={username}, path={sandbox_path}")
            else:
                log.warning(
                    f"上传文件存入沙箱失败（不阻塞后续处理）: user={username}, file={display_name}"
                )
    except Exception as e:
        log.warning(f"上传文件存入沙箱异常（不阻塞后续处理）: user={username}, err={e}")

    sandbox_hint: str = (
        f"\n\n（该文件已存入你的沙箱：`{sandbox_path}`。"
        f"如需重新读取、转换格式或在沙箱内计算，可用 sandbox_shell / read_file 工具操作它；"
        f"这个路径是你专属的，其他用户看不到。）"
    ) if sandbox_path else ""

    # —— 技能包（SKILL.md）：审计通过后装进本人沙箱的技能目录 ——
    if is_skill_file(file_path, display_name):
        log.info(f"技能包上传处理: user={username}, file={display_name}")
        async for _chunk in _handle_skill_upload(session_id, username, file_path, display_name):
            yield _chunk
        return

    # —— 图片：服务端预 OCR ——
    if is_image_file(file_path):
        log.info(f"图片上传处理(stream+ocr), file={file_path}")
        ocr_text: str | None = await asyncio.to_thread(_ocr_image, file_path)
        if ocr_text:
            if message:
                user_message = (
                    f"{message}\n\n"
                    f"用户上传了一张图片（{display_name}），图片内容如下：\n\n"
                    f"{ocr_text}\n\n"
                    f"请根据以上图片内容进行处理。{sandbox_hint}"
                )
            else:
                user_message = (
                    f"用户上传了一张图片（{display_name}），图片内容如下：\n\n"
                    f"{ocr_text}\n\n"
                    f"请根据以上图片内容进行处理。{sandbox_hint}"
                )
        else:
            # OCR 失败：直接告知用户，不让 Agent 尝试（避免幻觉死循环）
            log.warning(f"OCR 失败，不降级给 agent 处理: file={file_path}")
            error_msg = (
                f"抱歉，系统未能自动识别图片「{file_name}」中的文字内容。\n\n"
                f"请尝试以下方式：\n"
                f"1. 重新上传更清晰的图片\n"
                f"2. 直接输入图片中的文字内容\n"
                f"3. 发送文字描述，我可以帮你处理"
            )
            yield f"data: {json.dumps({'content': error_msg}, ensure_ascii=False)}\n\n"
            await _finish_turn(acc, username, session_id, _turn_t0)
            yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            return
        async for _chunk in process_chat_stream(session_id, username, user_message):
            yield _chunk
        return

    # —— 非表格文件（txt/md/csv/pdf/docx/zip…）：**不解析**，交给沙箱工具处理 ——
    # 以前不区分类型，一律走 pandas read_excel：上传 .txt/.csv/.pdf 会抛异常，
    # 用户只看到"抱歉，本轮处理出了点问题"。这里直接把"文件在哪"告诉模型。
    _ext: str = os.path.splitext(file_name)[1].lower()
    if _ext not in (".xlsx", ".xls", ".xlsm"):
        log.info(f"非表格文件上传，交由沙箱工具处理: file={file_name}, ext={_ext}")
        _msg: str = (
            (f"{message}\n\n" if message else "")
            + f"用户上传了文件「{display_name}」{sandbox_hint}\n"
            + "请先用 sandbox_shell 看一下它的类型/大小/内容（例如 `file`、`ls -l`、"
              "`head -c 2000`、`unzip -l`、`pdftotext`），再按用户的要求处理。"
        )
        async for _chunk in process_chat_stream(session_id, username, _msg):
            yield _chunk
        return

    # —— Excel：服务端 pandas 解析 ——
    try:
        json_output: str = await asyncio.to_thread(_parse_excel_file, file_path)
        parsed_data: dict = json.loads(json_output)

        if "error" in parsed_data:
            error_msg = f"Excel 解析出错: {parsed_data['error']}"
            yield f"data: {json.dumps({'error': error_msg}, ensure_ascii=False)}\n\n"
            await _finish_turn(acc, username, session_id, _turn_t0)
            yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            return

        columns: list = parsed_data.get("columns", [])
        row_count: int = parsed_data.get("row_count", 0)
        data: list = parsed_data.get("data", [])

        if row_count == 0:
            yield f"data: {json.dumps({'error': 'Excel 文件中没有数据。'}, ensure_ascii=False)}\n\n"
            await _finish_turn(acc, username, session_id, _turn_t0)
            yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"
            return

        summary: str = f"Excel 文件已解析，共 {row_count} 行数据:\n"
        summary += f"列名: {', '.join(str(c) for c in columns)}\n"
        summary += f"数据预览 (前5行):\n"
        for i, row in enumerate(data[:5]):
            non_empty: dict = {k: v for k, v in row.items() if v}
            summary += f"  [{i + 1}] {json.dumps(non_empty, ensure_ascii=False)}\n"
        summary += f"\n---\n"

        if message:
            user_message = f"{message}\n\n{summary}\n请根据以上 Excel 数据，帮我对信息进行处理，请先展示解析结果让我确认。{sandbox_hint}"
        else:
            user_message = f"{summary}\n请根据以上 Excel 数据，帮我对信息进行处理，请先展示解析结果让我确认。{sandbox_hint}"

        log.info(f"Excel 上传处理(stream), file={file_path}, rows={row_count}")
        async for _chunk in process_chat_stream(session_id, username, user_message):
            yield _chunk

    except json.JSONDecodeError as e:
        log.error(f"Excel 解析 JSON 错误: {e}")
        yield f"data: {json.dumps({'error': 'Excel 文件解析失败，请检查文件格式。'}, ensure_ascii=False)}\n\n"
        await _finish_turn(acc, username, session_id, _turn_t0)
        yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"
    except Exception as e:
        log.error(f"文件上传处理失败: {e}", exc_info=True)
        yield f"data: {json.dumps({'error': _safe_error_str(e)}, ensure_ascii=False)}\n\n"
        await _finish_turn(acc, username, session_id, _turn_t0)
        yield f"data: {json.dumps({'done': True, 'session_id': session_id}, ensure_ascii=False)}\n\n"