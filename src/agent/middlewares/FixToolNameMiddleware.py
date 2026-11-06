"""
中间件：幻觉工具名安全网

由于 agent 不使用 LocalShellBackend（无文件系统/shell 工具），
中间件仅负责检测和修正 LLM 偶尔产生的重复拼接工具名（如 executeexecute）：
1. 幻觉工具名检测与修正 — 重复拼接的工具名修正后透传给 handler
2. 连续幻觉退化检测 — ≥3 次无效工具名，抛异常终止
"""
import threading

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.infra.utils.LogUtil import log

def _valid_tools() -> set[str]:
    """工具名白名单：直接从注册表派生，加新工具不用再改这里（懒加载避免循环导入）"""
    try:
        from src.agent.tools.registry import REGISTRY
        return {s.name for s in REGISTRY}
    except Exception:
        return set()   # 注册表都导入不了时，agent 本就起不来，不做产品相关的兜底

# ========== 幻觉计数器（线程局部，每条请求独立） ==========

_thread_local: threading.local = threading.local()
_MAX_CONSECUTIVE: int = 3


def _get_counter() -> int:
    return getattr(_thread_local, 'hallucination_count', 0)


def _inc_counter() -> int:
    count = _get_counter() + 1
    _thread_local.hallucination_count = count
    return count


def _reset_counter() -> None:
    _thread_local.hallucination_count = 0


class HallucinationLoopError(RuntimeError):
    """幻觉退化循环异常，强制终止 agent"""
    def __init__(self, tool_name: str, corrected: str, count: int):
        super().__init__(
            f"幻觉退化循环: 连续 {count} 次无效工具名 "
            f"(最后一次: {tool_name} -> {corrected})，已终止。"
        )


# ========== 通用幻觉检测（非硬编码） ==========

def _detect_hallucination(tool_name: str) -> str | None:
    """通用检测：工具名是否为某个有效工具名重复拼接 >= 2 次"""
    for valid in _valid_tools():
        if not tool_name.startswith(valid):
            continue
        if len(tool_name) % len(valid) != 0:
            continue
        repeat_count = len(tool_name) // len(valid)
        if repeat_count < 2:
            continue
        if tool_name == valid * repeat_count:
            return valid
    return None


# ========== 中间件 ==========

class FixToolNameMiddleware(AgentMiddleware):
    """幻觉工具名修正中间件

    对所有工具调用执行：
    1. 幻觉检测 — 识别重复拼接的工具名并修正
    2. 连续幻觉退化检测 — 超过阈值抛异常终止
    """

    def _before(self, request: ToolCallRequest) -> None:
        """幻觉检测与修正（同步/异步共用；可能抛 HallucinationLoopError）"""
        tool_call = request.tool_call
        tool_name = tool_call.get("name", "")
        tool_call_id = tool_call.get("id", "")

        corrected_name = _detect_hallucination(tool_name)

        if corrected_name is None:
            _reset_counter()
            return

        count = _inc_counter()
        log.warning(
            f"幻觉工具名已修正: {tool_name} -> {corrected_name} (连续={count})"
        )

        if count > _MAX_CONSECUTIVE:
            _reset_counter()
            raise HallucinationLoopError(tool_name, corrected_name, count)

        tool_call["name"] = corrected_name

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage | Command:
        self._before(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler,
    ) -> ToolMessage | Command:
        # astream 路径：基类没有 awrap_tool_call 的默认实现
        self._before(request)
        return await handler(request)
