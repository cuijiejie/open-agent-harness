"""把仓库里的 AGENT.md 注入系统提示（**宿主读文件，不碰沙箱**）

背景（2026-10-30 实测）：
    deepagents 的 memory 中间件按**沙箱路径**读 `memory=["/AGENT.md"]`，
    但沙箱是独立容器根（overlay），里面根本没有 /AGENT.md ——
    每次新会话都白跑一次 sandbox exec（实测 1.1~3.7 秒）拿到的是"文件不存在"，
    仓库里那份 19KB 的 AGENT.md **一直没进模型**。

改法：直接读宿主仓库的 AGENT.md（部署机上那份就是权威版本），拼到系统提示末尾。
零沙箱往返，内容一定是最新仓库版本。

失败不影响主流程（读不到就不注入）。
"""

from __future__ import annotations

import os
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from src.agent.middlewares.PermissionHintMiddleware import _append_to_system
from src.infra.utils.LogUtil import log

# 单次注入上限：AGENT.md 正常约 19KB，超过这个量级多半是误换了文件，
# 截断并告警，避免把系统提示撑爆（token 成本会失控）
_MAX_CHARS: int = 60_000


def _repo_root() -> str:
    """仓库根 = src/agent/middlewares 往上三层"""
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )


def load_agent_md(path: str = "") -> str:
    """读 AGENT.md 内容（读不到返回空串）"""
    p: str = path or os.path.join(_repo_root(), "AGENT.md")
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            text: str = fh.read()
    except OSError as e:
        log.warning(f"读取 AGENT.md 失败（不注入）: path={p}, err={e}")
        return ""
    if len(text) > _MAX_CHARS:
        log.warning(f"AGENT.md 过大，已截断: {len(text)} 字符 → {_MAX_CHARS}")
        text = text[:_MAX_CHARS]
    return text.strip()


class HostMemoryMiddleware(AgentMiddleware):
    """把 AGENT.md 内容追加到系统提示（每个 agent 实例只读一次文件）"""

    def __init__(self, path: str = "") -> None:
        super().__init__()
        self._text: str = load_agent_md(path)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self._text:
            return handler(request)
        try:
            _append_to_system(request, self._text)
        except Exception as e:
            log.debug(f"AGENT.md 注入失败（忽略）: {e}")
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        # 异步路径必须单独实现（基类的 awrap_model_call 会直接抛 NotImplementedError）
        if not self._text:
            return await handler(request)
        try:
            _append_to_system(request, self._text)
        except Exception as e:
            log.debug(f"AGENT.md 注入失败（忽略）: {e}")
        return await handler(request)

    def before_model(self, state: Any, runtime: Any = None) -> dict | None:
        return None
