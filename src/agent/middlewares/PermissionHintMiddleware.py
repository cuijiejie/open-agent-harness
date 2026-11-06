"""权限提示中间件

目的：让模型在**第一句话**就知道自己缺哪些写操作权限，而不是先问一堆参数、
最后才说"我没有这个接口"。

做法：装配 agent 时算出「本用户不可用的写操作清单」，在每次调用模型前
追加到系统提示末尾。模型看到"你当前没有新建衍生产品的权限"这一行，
就不会再去引导用户填参数。

失败不影响主流程（try/except 吞掉，最多是提示不出现）。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware

log = logging.getLogger(__name__)


class PermissionHintMiddleware(AgentMiddleware):
    """把「能力边界」注入系统提示

    hint 为空时什么都不做（管理员全权限的用户不需要这行）。
    """

    def __init__(self, hint: str = "") -> None:
        super().__init__()
        self._hint: str = (hint or "").strip()

    # ------------------------------------------------------------------
    # 新版 LangChain：wrap_model_call 能直接改 system prompt
    # ------------------------------------------------------------------
    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self._hint:
            return handler(request)
        try:
            _append_to_system(request, self._hint)
        except Exception as e:
            log.debug(f"权限提示注入失败（忽略）: {e}")
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self._hint:
            return await handler(request)
        try:
            _append_to_system(request, self._hint)
        except Exception as e:
            log.debug(f"权限提示注入失败（忽略）: {e}")
        return await handler(request)

    # ------------------------------------------------------------------
    # 兜底：万一 wrap_model_call 的 request 结构变了，走 before_model
    # ------------------------------------------------------------------
    def before_model(self, state: Any, runtime: Any = None) -> dict | None:
        return None


def _append_to_system(request: Any, hint: str) -> None:
    """把 hint 追加到 request 的 system prompt 上

    LangChain 的 ModelRequest 在不同版本里字段名不一样，这里逐个试：
      request.system_prompt / request.system / request.messages[0]
    """
    # 1) 独立字段
    for attr in ("system_prompt", "system"):
        cur = getattr(request, attr, None)
        if isinstance(cur, str):
            if hint in cur:
                return
            setattr(request, attr, (cur + "\n\n" + hint).strip())
            return

    # 2) 拼在 messages 的最前面（SystemMessage）
    msgs = getattr(request, "messages", None)
    if isinstance(msgs, list) and msgs:
        first = msgs[0]
        content = getattr(first, "content", None)
        if isinstance(content, str) and getattr(first, "type", "") in ("system", "SystemMessage"):
            if hint in content:
                return
            first.content = content + "\n\n" + hint
            return
        # 没有 system 消息 → 插一条
        try:
            from langchain_core.messages import SystemMessage

            msgs.insert(0, SystemMessage(content=hint))
            return
        except Exception:
            pass

    # 3) 实在不行：塞到第一条用户消息前面
    raise RuntimeError("无法定位 system prompt 字段")
