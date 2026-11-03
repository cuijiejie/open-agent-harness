"""分阶段计时：定位「用户觉得慢」到底慢在哪一段

默认**完全静默**（不产生任何日志、不做时间计算）；
需要排查时在服务环境里加 `PA_TIMING=1` 再重启，就会在 logs/app.log 打出形如：

    [timing session=abc123] ③ 构建 agent: 8,240ms (累计 8,330ms)
    [timing session=abc123] ⑧ 首字: 420ms (累计 9,100ms)

设计成"零成本开关"而不是临时 print：这类问题会反复出现（新会话第一句慢、
某个 worker 偶发慢），留着一个统一开关比每次改代码重发版划算。
"""
from __future__ import annotations

import os
import time

_ENABLED: bool = os.environ.get("PA_TIMING", "") in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _ENABLED


class Marks:
    """一段流程的分段计时器（不启用时所有方法都直接返回）"""

    __slots__ = ("tag", "t0", "last")

    def __init__(self, tag: str = "") -> None:
        self.tag = tag
        self.t0 = time.perf_counter()
        self.last = self.t0

    def mark(self, label: str) -> float:
        """打一条「本段耗时 + 累计耗时」，返回本段毫秒数（未启用返回 0）"""
        if not _ENABLED:
            return 0.0
        now = time.perf_counter()
        seg = (now - self.last) * 1000
        total = (now - self.t0) * 1000
        self.last = now
        try:
            from src.infra.utils.LogUtil import log

            prefix = f" {self.tag}" if self.tag else ""
            log.info(f"[timing{prefix}] {label}: {seg:.0f}ms (累计 {total:.0f}ms)")
        except Exception:
            pass
        return seg
