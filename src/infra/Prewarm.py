"""页面加载后的后台预热：把「新会话第一句」的冷成本挪到用户打字期间

只需要预热两件事（其余已经在 Redis / 进程里缓存住了）：
  1. 技能自带工具扫描 → 写 Redis（跨 worker 共享）
  2. MCP 工具清单 → 本进程内缓存（每个 worker 首次才付 1.8 秒）

为什么值得做：新会话第一句要构建 agent，构建时会扫技能、拉 MCP；
这两件事在**用户打开页面那一刻**就可以做掉，等他打完字发消息时基本已经完成。

约束：
  · 只读操作，不改任何业务状态 → 对功能没有影响
  · 后台线程，失败一律吞掉（预热失败不能影响页面加载）
  · 同一用户 120 秒内最多预热一次（防刷）
"""
from __future__ import annotations

import threading
import time

from src.infra.utils.LogUtil import log

_WARM_TTL: float = 120.0
_last: dict[str, float] = {}
_lock = threading.Lock()


def _should_warm(username: str, now: float | None = None) -> bool:
    """节流判断（纯函数，便于单测）"""
    if not username:
        return False
    t = time.time() if now is None else now
    with _lock:
        last = _last.get(username, 0.0)
        if t - last < _WARM_TTL:
            return False
        _last[username] = t
    return True


def _do_warm(username: str) -> None:
    try:
        from src.agent.mcp.client import mcp_manager

        mcp_manager.load_tools()
    except Exception as e:
        log.debug(f"预热 MCP 工具失败（忽略）: {e}")
    try:
        from src.infra.SkillToolsService import load_skill_tools

        load_skill_tools(username)
    except Exception as e:
        log.debug(f"预热技能工具失败（忽略）: {e}")


def warm_user(username: str) -> bool:
    """后台预热（立即返回）。返回 False = 被节流或参数非法"""
    if not _should_warm(username):
        return False
    threading.Thread(target=_do_warm, args=(username,), daemon=True,
                     name=f"prewarm-{username}").start()
    log.info(f"已触发后台预热: user={username}")
    return True


def reset() -> None:
    """清空节流记录（测试用）"""
    with _lock:
        _last.clear()
