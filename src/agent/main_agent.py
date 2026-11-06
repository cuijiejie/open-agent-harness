"""
Agent 服务模块
负责 LLM 模型管理、deep agent 创建和缓存生命周期管理

安全设计：不注入 LocalShellBackend，agent 只能使用 tools/ 中定义的
若干内置 tool（ask_user / sandbox_shell / 记忆读写）+ 插件里的业务工具，
无宿主机文件系统访问和宿主机 shell 执行能力（文件与命令只能落进用户的隔离沙箱）。
图片 OCR 由服务端预处理后传给 agent，无需 agent 自行调用 OCR 工具。
TODO: 升级 langgraph >= 1.3 以支持 stream_events(version="v3") 流式协议，
      当前使用 stream(stream_mode="messages") 旧版协议。
      状态持久化已由 MemorySaver 升级为 PostgresSaver（PostgreSQL 连接池），
      服务重启/多进程共享会话状态均不再丢失，并支持多机分布式部署。
"""
import json
import re
import threading
import time
from collections import OrderedDict
from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph.state import CompiledStateGraph
from src.infra.utils.LogUtil import log
from config.Configs import cfg
from src.agent.middlewares.FixToolNameMiddleware import FixToolNameMiddleware
from src.agent.middlewares.HostMemoryMiddleware import HostMemoryMiddleware
from src.agent.middlewares.PermissionHintMiddleware import PermissionHintMiddleware
from src.agent.middlewares.ToolOutputGuardMiddleware import ToolOutputGuardMiddleware
from src.agent.tools import ALL_TOOLS
from src.infra.CheckpointerManager import get_checkpointer


# ========== 禁用 SummarizationMiddleware（必须在 import create_deep_agent 之前）==========
# deepagents 内置的 SummarizationMiddleware 截断消息时会破坏 tool/assistant 配对，
# 导致 DeepSeek API 报 400 错误。此处替换为直接透传，不做任何截断。

from deepagents.middleware import summarization as _summarization_mod
from langchain.agents.middleware.types import AgentMiddleware


class _NoOpSummarizationMiddleware(AgentMiddleware):
    """直接透传，不做消息截断"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        # astream/ainvoke 走这条：LangGraph 的中间件基类对 wrap_model_call
        # 有同步默认实现，但异步版本没有默认实现，只写同步版会直接 NotImplementedError
        return await handler(request)


_summarization_mod.SummarizationMiddleware = _NoOpSummarizationMiddleware

from deepagents import create_deep_agent


# ========== 自定义 ChatOpenAI：保留 reasoning_content ==========

class ChatOpenAIWithReasoning(ChatOpenAI):
    """ChatOpenAI 子类，保留 reasoning_content 用于推理模型多轮对话。

    ChatOpenAI 的 _convert_delta_to_message_chunk 忽略 reasoning_content 字段，
    导致推理模型的思维链在流式处理和多轮对话中丢失。
    本子类将 reasoning_content 存入 additional_kwargs，确保 LangGraph state 中保留。
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        gen_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen_chunk is None:
            return None

        # 从 delta 中提取 reasoning_content，存入 additional_kwargs
        choices = chunk.get("choices", [])
        if choices and choices[0].get("delta"):
            delta = choices[0]["delta"]
            reasoning = delta.get("reasoning_content", "")
            if reasoning:
                existing = gen_chunk.message.additional_kwargs.get("reasoning_content", "")
                gen_chunk.message.additional_kwargs["reasoning_content"] = existing + reasoning

        return gen_chunk

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """在请求中回传 reasoning_content，保持推理链连续性"""
        from langchain_core.messages import AIMessage
        lc_messages = self._convert_input(input_).to_messages()
        reasoning_map: dict[int, str] = {}
        for i, m in enumerate(lc_messages):
            if isinstance(m, AIMessage):
                rc = m.additional_kwargs.get("reasoning_content", "")
                if rc:
                    reasoning_map[i] = rc

        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        if reasoning_map and "messages" in payload:
            for i, msg in enumerate(payload["messages"]):
                if i in reasoning_map and msg.get("role") == "assistant":
                    msg["reasoning_content"] = reasoning_map[i]

        return payload


# ========== 跨 worker 的缓存失效广播 ==========
# agent 缓存是**进程内** LRU（无 TTL），而角色/技能/工具开关是全局事实：
# 只 evict 当前进程，gunicorn 其它 worker 会一直用旧工具清单，直到重启或 LRU 淘汰。
# 用 Redis 里一个递增版本号做"广播"，各 worker 每秒最多比对一次，发现变化就清空本地缓存。
_CACHE_EPOCH_KEY: str = "agent:cache:epoch"
_EPOCH_CHECK_TTL: float = 2.0


def _read_epoch() -> str:
    try:
        from src.infra.utils import CacheUtil

        return str(CacheUtil.get(_CACHE_EPOCH_KEY) or "0")
    except Exception:
        return "0"


def _bump_epoch() -> None:
    """通知所有 worker：agent 缓存已失效（Redis 版本号 +1）"""
    try:
        from src.infra.utils import CacheUtil

        cur: int = int(_read_epoch() or 0)
        # TTL 给足（30 天），避免 key 过期后版本号回绕造成周期性误清
        CacheUtil.set(_CACHE_EPOCH_KEY, cur + 1, 30 * 24 * 3600)
    except Exception:
        pass


# ========== Agent 管理器 ==========

class AgentManager:
    """Agent 实例管理器：LLM 模型 + checkpointer + agent 缓存

    封装全局状态便于测试和依赖注入，消除模块级可变全局变量。
    """

    def __init__(self) -> None:
        # checkpointer 由 lifespan 里的 init_async 创建；这里**不能**在 import 期取，
        # 否则模块级单例（_agent_manager = AgentManager()）一 import 就抛错。走下面的属性懒取。
        self._cache: OrderedDict[str, CompiledStateGraph] = OrderedDict()
        self._cache_lock: threading.Lock = threading.Lock()
        self._cache_max: int = 128
        self._epoch_local: str = _read_epoch()
        self._epoch_checked: float = 0.0
        self._model: ChatOpenAIWithReasoning = ChatOpenAIWithReasoning(
            model=cfg.deepseekModel,
            base_url=cfg.deepseekBaseUrl,
            api_key=cfg.deepseekApiKey,
            streaming=True,
            temperature=0,
            request_timeout=120,
        )

    @property
    def _checkpointer(self):
        """懒获取 AsyncPostgresSaver（由 lifespan 初始化）"""
        return get_checkpointer()

    async def create_agent(self, session_id: str, username: str) -> CompiledStateGraph:
        """创建或从缓存获取 deep agent 实例

        - 仅注入 7 个业务 tool，不注入文件系统/shell 工具（无 LocalShellBackend）
        - AGENT.md：由 memory 参数通过 MemoryMiddleware 加载
        - 会话隔离：由 checkpointer 保证
        - agent 缓存：按 <session_id>_<username> 缓存，LRU 淘汰
        """
        cache_key: str = f"{session_id}_{username}"

        self._maybe_sync_epoch()

        with self._cache_lock:
            if cache_key in self._cache:
                agent = self._cache[cache_key]
                self._cache.move_to_end(cache_key)
                log.info(f"命中缓存: session=<{session_id}>, user=<{username}>")
                return agent

        log.info(f"创建 Agent: session=<{session_id}>, user=<{username}>")

        from src.infra.Timing import Marks

        _mk = Marks(f"agent user={username}")

        # 沙箱后端：把 deepagents 内置文件工具（ls / read_file / write_file /
        # edit_file / glob / grep）接到「该用户自己的 OpenShell 沙箱」。
        # 不传的话这些内置工具会静默返回空结果，模型据此误判「文件不存在」，
        # 并且永远不会去用 sandbox_shell（实测：agent 调 glob/ls 得空后答「沙箱是空的」）。
        from src.agent.backends.openshell import build_backend
        _backend = build_backend(username)
        _mk.mark("a 沙箱后端")
        _backend_kwargs: dict = {"backend": _backend} if _backend is not None else {}
        if _backend is not None:
            log.info(f"启用沙箱后端: id={_backend.id}")

        # 中间件：工具名修正 + 用户技能包加载
        # HostMemoryMiddleware：把仓库里的 AGENT.md 注入系统提示。
        # 之前用 deepagents 的 memory=["/AGENT.md"]，但它按**沙箱路径**读，
        # 沙箱里没有这个文件 → 每次新会话白跑一次 exec（1~3.7 秒）且内容从没注入过。
        _middlewares: list = [
            FixToolNameMiddleware(),
            ToolOutputGuardMiddleware(),
            HostMemoryMiddleware(),
        ]

        # 能力边界提示：把「缺哪些写操作」提前告诉模型，
        # 避免它先问一堆参数、最后才说"我没有这个接口"。
        try:
            _hint: str = _build_permission_hint(username)
            _mk.mark("a1 权限边界提示")
            if _hint:
                _middlewares.append(PermissionHintMiddleware(_hint))
                log.info(f"权限边界提示已注入: user={username}, 缺 {len(_missing_write_tools(username))} 项写操作")
        except Exception as e:
            log.warning(f"权限边界提示注入失败（不影响主流程）: {e}")
        if _backend is not None:
            # 技能包（用户上传的 SKILL.md）：SkillsMiddleware 通过沙箱后端读取
            # /sandbox/skills/ 下的技能说明并注入系统提示词，让智能体知道
            # 「这台沙箱的主人装了哪些自定义能力」。
            # 技能文件在本人沙箱内 → 天然按用户隔离，其他用户看不到。
            # 失败不阻塞主流程（没有技能包时也应该能正常对话）。
            try:
                from deepagents.middleware.skills import SkillsMiddleware

                _middlewares.append(
                    SkillsMiddleware(backend=_backend, sources=["/sandbox/skills/"])
                )
                log.info("启用技能中间件: source=/sandbox/skills/")
            except Exception as e:
                log.warning(f"技能中间件初始化失败（不影响主流程）: {e}")

        # 技能自带工具：该用户沙箱里技能声明的工具（/sandbox/skills/*/tools.json）
        # 注册进本次 agent 的工具清单 —— 命令只在沙箱里执行，宿主只负责拼命令（见 SkillToolsService）
        _skill_tools: list = []
        if _backend is not None:
            try:
                from src.infra.SkillToolsService import load_skill_tools

                # 按需披露（[agent] skill_tools_lazy=true）：只给"本会话读过 SKILL.md"的技能注册工具。
                # 技能多了能省一大截提示词；读技能后由 ChatService 清缓存，下一轮工具才出现。
                only: set[str] | None = None
                if bool(getattr(cfg, "skillToolsLazy", False)):
                    only = await self._read_skills_in_session(session_id, username)
                _skill_tools = load_skill_tools(username, only_skills=only)
                _mk.mark("a3 技能工具加载")
                if only is not None:
                    log.info(f"技能工具按需披露: user={username}, 已读技能={sorted(only)}, 注册={len(_skill_tools)}")
            except Exception as e:
                log.warning(f"加载技能自带工具失败（不影响主流程）: {e}")

        # 注意：技能包是**用户自己的**，不参与角色分配 → 任何角色都能用自己的技能工具，
        # 这里不做 RBAC 过滤（角色只管「内置工具」和「MCP 工具」）。

        _mk.mark("b1 技能自带工具")

        # 工具授权：按用户名解析工具清单（没权限的工具**不进清单** → 模型看不到也调不到）
        from src.agent.tools import resolve_tools

        _base_tools: list = resolve_tools(username)
        _mk.mark("b 内置工具/RBAC")

        # MCP 工具（外部服务）：管理员在 [mcp] 里声明了才接入；再按角色工具清单过滤
        _mcp_tools: list = []
        try:
            from src.agent.mcp.client import mcp_manager
            from src.infra.RbacService import rbac as _rbac2

            _all_mcp: list = mcp_manager.load_tools()
            # 「公共」标记读库（pa_tools.always_on），不再用代码常量；
            # 读不到时回落到常量，避免 DB 抖动把公共 MCP 工具藏起来
            try:
                from src.infra.ToolMetaService import tool_meta as _tm_mcp

                _public_mcp: set = set(_tm_mcp.public_names())
            except Exception:
                _public_mcp = set()
            if not _public_mcp:
                from src.agent.tools.registry import ALWAYS_ON_MCP_NAMES
                _public_mcp = set(ALWAYS_ON_MCP_NAMES)

            _mk.mark("c MCP 工具列表")
            _mcp_tools = [
                t for t in _all_mcp
                if str(getattr(t, "name", "")) in _public_mcp
                or _rbac2.allows_tool(username, str(getattr(t, "name", "")))
            ]
            if len(_mcp_tools) != len(_all_mcp):
                log.info(f"RBAC: MCP 工具按角色过滤 {len(_all_mcp)} → {len(_mcp_tools)}: user={username}")
        except Exception as e:
            log.warning(f"接入 MCP 工具失败（不影响主流程）: {e}")

        _mk.mark("d 技能工具")
        log.info(f"工具装配: user={username}, 数量={len(_base_tools)}, 技能工具={len(_skill_tools)}, "
                 f"MCP 工具={len(_mcp_tools)}")

        # 子代理开关（[agent] enable_subagent=true 才放开）。
        # 默认关闭：单场景产品查询用不到，放开只会多烧 token、多一层不可控；
        # 需要"批量分析多个文件/长链路任务"时把开关打开即可（框架自带 general-purpose 子代理）。
        _subagents: list = [] if bool(getattr(cfg, "enableSubagent", False)) else [{
            "name": "general-purpose",
            "description": "已禁用",
            "system_prompt": "该子代理已被禁用，不要使用 task 工具。",
            "tools": [],
        }]

        agent: CompiledStateGraph = create_deep_agent(
            model=self._model,
            memory=[],          # AGENT.md 改由 HostMemoryMiddleware 从宿主注入（见上）
            tools=[*_base_tools, *_skill_tools, *_mcp_tools],
            checkpointer=self._checkpointer,
            middleware=_middlewares,
            name="open_agent_harness",
            subagents=_subagents,
            **_backend_kwargs,
        )

        _mk.mark("e create_deep_agent 编译")
        log.info(f"Agent 创建完成: session=<{session_id}>, user=<{username}>")

        with self._cache_lock:
            if cache_key in self._cache:
                existing = self._cache[cache_key]
                self._cache.move_to_end(cache_key)
                log.info(f"并发冲突，丢弃当前实例: session=<{session_id}>")
                return existing

            self._cache[cache_key] = agent
            self._cache.move_to_end(cache_key)

            while len(self._cache) > self._cache_max:
                evicted_key, _ = self._cache.popitem(last=False)
                log.info(f"LRU 淘汰: <{evicted_key}>, 当前缓存: {len(self._cache)}")


        return agent

    async def _read_skills_in_session(self, session_id: str, username: str) -> set[str] | None:
        """本会话读过哪些技能的 SKILL.md（按需披露用）

        判断依据：会话消息里 read_file / sandbox_shell 的工具调用参数中出现
        `/sandbox/skills/<技能名>/`。**出错时返回 None**（= 退化成全量注册），
        宁可多注册也不让工具凭空消失。
        """
        try:
            thread_id: str = f"{session_id}_{username}"
            tup = await self._checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
            if not tup:
                return set()
            values: dict = (getattr(tup, "checkpoint", None) or {}).get("channel_values", {}) or {}
            messages: list = values.get("messages", []) or []
            found: set[str] = set()
            for m in messages:
                for c in (getattr(m, "tool_calls", None) or []):
                    if str(c.get("name") or "") not in ("read_file", "sandbox_shell"):
                        continue
                    args: str = json.dumps(c.get("args") or {}, ensure_ascii=False)
                    found.update(re.findall(r"/sandbox/skills/([A-Za-z0-9._-]+)/", args))
            return found
        except Exception as e:
            log.warning(f"读取会话技能使用情况失败（退化为全量注册）: {e}")
            return None

    def _maybe_sync_epoch(self) -> None:
        """比对 Redis 里的失效版本号；与本地记录不一致就清空本进程缓存

        节流：每 _EPOCH_CHECK_TTL 秒最多查一次 Redis（每条消息一次 GET 太浪费）。
        Redis 不可用时读到 "0"，与本地一致 → 退化为原来的"只清本进程"行为。
        """
        now: float = time.time()
        if now - self._epoch_checked < _EPOCH_CHECK_TTL:
            return
        self._epoch_checked = now
        cur: str = _read_epoch()
        if cur != self._epoch_local:
            with self._cache_lock:
                n: int = len(self._cache)
                self._cache.clear()
            self._epoch_local = cur
            if n:
                log.info(f"收到其他进程的缓存失效广播（epoch={cur}），已清空本地 agent 缓存 {n} 个")

    def _broadcast(self) -> None:
        """把自己刚做的清理广播给其它 worker，并同步本地版本号（避免重复清）"""
        _bump_epoch()
        self._epoch_local = _read_epoch()
        self._epoch_checked = time.time()

    def evict_from_cache(self, session_id: str, username: str) -> bool:
        """从缓存中移除指定 session 的 agent 实例"""
        cache_key: str = f"{session_id}_{username}"
        with self._cache_lock:
            if cache_key in self._cache:
                del self._cache[cache_key]
                log.info(f"会话删除，清除缓存: <{cache_key}>, 当前缓存: {len(self._cache)}")
                removed: bool = True
            else:
                removed = False
        self._broadcast()
        return removed

    async def delete_thread(self, session_id: str, username: str) -> bool:
        """删除指定会话在 checkpointer（PostgreSQL）中的所有 checkpoint 和 writes。

        thread_id 规则与 ChatService 一致：<session_id>_<username>。
        会话删除时同步清理，避免 checkpointer 中累积孤儿状态。
        """
        thread_id: str = f"{session_id}_{username}"
        try:
            await self._checkpointer.adelete_thread(thread_id)
            log.info(f"checkpoint 已删除: thread_id=<{thread_id}>")
            return True
        except Exception as e:
            log.warning(f"删除 checkpoint 失败: thread_id=<{thread_id}>, 错误: {e}")
            return False


# 全局单例（保持向后兼容的模块级访问）
_agent_manager: AgentManager = AgentManager()

# ========== 向后兼容的函数接口 ==========

def get_model():
    """返回全局 Agent 共用的模型实例（供上下文摘要等内部调用）"""
    return _agent_manager._model


async def create_agent(session_id: str, username: str) -> CompiledStateGraph:
    return await _agent_manager.create_agent(session_id, username)


def evict_agent_from_cache(session_id: str, username: str) -> bool:
    return _agent_manager.evict_from_cache(session_id, username)


def evict_all_agents() -> int:
    """清空**全部** agent 缓存（MCP 重载这类全局变动后调用），返回清掉的数量

    同时广播给其它 worker：只清本进程的话，另外 5 个 worker 会继续拿旧工具清单。
    """
    mgr = _agent_manager
    with mgr._cache_lock:
        n: int = len(mgr._cache)
        mgr._cache.clear()
    mgr._broadcast()
    if n:
        log.info(f"全局配置变动，清空 agent 缓存 {n} 个")
    return n


def evict_agent_cache_by_user(username: str) -> int:
    """清掉该用户**所有会话**的 agent 缓存（技能/工具变动后调用），返回清掉的数量。

    为什么不按 session 清：技能是用户级的（在本人沙箱里），一处变动要
    让该用户所有会话都重新装配，否则用户切回旧会话会发现"装了没用"。

    广播：同一用户的请求可能落到别的 worker，所以必须让全体 worker 一起失效。
    """
    mgr = _agent_manager
    suffix: str = f"_{username}"
    removed: int = 0
    with mgr._cache_lock:
        for key in list(mgr._cache.keys()):
            if key.endswith(suffix):
                mgr._cache.pop(key, None)
                removed += 1
    mgr._broadcast()
    if removed:
        log.info(f"技能/工具变动，清理 agent 缓存 {removed} 个: user={username}")
    return removed


async def delete_thread(session_id: str, username: str) -> bool:
    return await _agent_manager.delete_thread(session_id, username)


# ---------------------------------------------------------------------------
# 能力边界：算出「本用户缺哪些写操作」
# ---------------------------------------------------------------------------
def _write_tools_all() -> list[tuple[str, str]]:
    """所有写类工具 [(工具名, 中文名)]

    数据源 pa_tools（risk in write/delete 且 enabled=1 且非 ghost）。
    失败返回空列表 → 上层不注入提示，模型行为退化成现状。
    """
    try:
        from src.infra.ToolMetaService import tool_meta

        from src.agent.tools.registry import ALWAYS_ON_NAMES, ALWAYS_ON_MCP_NAMES

        _skip = set(ALWAYS_ON_NAMES) | set(ALWAYS_ON_MCP_NAMES)
        out: list[tuple[str, str]] = []
        for name, meta in (tool_meta.all() or {}).items():
            if str(meta.get("risk") or "") not in ("write", "delete"):
                continue
            if not meta.get("enabled", True) or meta.get("ghost"):
                continue
            if str(name) in _skip:      # 人人可用的不进「能力边界」，否则满是噪音
                continue
            out.append((str(name), str(meta.get("display_name") or name)))
        return sorted(out)
    except Exception:
        return []


def _missing_write_tools(username: str) -> list[tuple[str, str]]:
    """本用户没有权限的写类工具"""
    try:
        from src.infra.RbacService import rbac

        if rbac.is_admin(username):
            return []
        return [(n, d) for n, d in _write_tools_all() if not rbac.allows_tool(username, n)]
    except Exception:
        return []


def _all_tool_labels() -> list[tuple[str, str]]:
    """[(内部名, 中文名)]：内置 + 插件 + 本账号可能拿到的 MCP 工具

    说明：MCP 工具不在 REGISTRY 里（由各服务自己声明），名字统一前缀 mcp_，
    从 pa_tools 取中文名。
    """
    out: list[tuple[str, str]] = []
    try:
        from src.agent.tools.registry import REGISTRY
        from src.infra.ToolMetaService import tool_meta

        meta = tool_meta.all() or {}
        for s in REGISTRY:
            m = meta.get(s.name) or {}
            out.append((s.name, str(m.get("display_name") or s.name)))
    except Exception:
        pass
    try:
        from src.infra.ToolMetaService import tool_meta

        meta = tool_meta.all() or {}
        for name, m in meta.items():
            if str(name).startswith("mcp_") and not any(n == name for n, _ in out):
                out.append((str(name), str(m.get("display_name") or name)))
    except Exception:
        pass
    return out


_STYLE_RULES: list[str] = [
    "## 对话风格（必须遵守）",
    "1. 用户只是打招呼（「你好」「在吗」「嗨」）时，**只回一句问候并问需求**，"
    "就是「你好，有什么可以帮你的？」这一句——不要罗列能力清单、不要主动提沙箱里的文件、"
    "不要主动建议做什么；",
    "2. 用户明确问「你能做什么」「有哪些功能」时，才列举能力，只说你实际拥有的工具，"
    "**不要**照抄平台通用功能说明；",
]


def _build_permission_hint(username: str) -> str:
    """生成注入系统提示的「能力边界 + 对话风格」片段

    三件事：
      ① 打招呼只回一句（所有人都要，包括管理员——不然管理员答「你好」照样罗列一堆）；
      ② 列出**本账号实际可用**的工具，和**未开通**的工具 —— 防止模型照着通用说明
         介绍一堆自己没有的功能（用户看到会以为系统坏了）；
      ③ 写操作缺失时，要求第一句就拒绝，别先问参数再绕圈。
    """
    try:
        from src.infra.RbacService import rbac

        if rbac.is_admin(username):
            return chr(10).join(_STYLE_RULES)

        pairs: list[tuple[str, str]] = _all_tool_labels()
        have: list[str] = [d for n, d in pairs if rbac.allows_tool(username, n)]
        miss: list[str] = [d for n, d in pairs if not rbac.allows_tool(username, n)]
        missing_write: list[tuple[str, str]] = _missing_write_tools(username)

        lines: list[str] = [
            "## 能力边界（必须遵守）",
            "",
            "下面是**本账号**的真实能力，界面上的权限就是按它判定的：",
            "",
            "**你实际可用的工具（只有这些）**：",
            *([f"- {d}" for d in have] or ["- （无业务工具，仅能对话）"]),
            "",
        ]
        if miss:
            lines += [
                "**本账号未开通的工具**：",
                *[f"- {d}" for d in miss],
                "",
            ]
        lines += [
            *_STYLE_RULES,
            "",
            "3. 用户要求「未开通」里的功能时，**第一句话就说**「当前账号没有开通 X」，"
            "并建议联系管理员开通；",
            "4. **不要**先问参数、不要假装能查、不要用沙箱写代码去猜业务数据来凑答案。",
        ]
        if missing_write:
            lines += [
                "",
                "**关于写操作（新建/修改/删除）**：你没有这些权限——",
                *[f"- {d}（{n}）" for n, d in missing_write],
                "",
                "对方要求写操作时：**第一句就说**「抱歉，我没有这个权限」，"
                "不要先问参数、不要先做分析、不要提议「我帮你打开页面你自己手工做」。",
            ]
        return chr(10).join(lines)
    except Exception:
        return ""
