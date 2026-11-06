#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""路由回归检查：Flask controller 删除后，用**冻结基线**防止路由被误删

背景：迁移期用「扫 src/controller 推导期望值」做验收（62/62 PASS）。Flask 删掉后
没有对照物了，所以改成两条硬约束：

  1. **数量下限**：业务端点（去掉系统路由）不少于 BASELINE_MIN 条
  2. **关键路径抽查**：核心接口必须在（少一条就是功能没了）

跑法：`python test/unit/route_contract.py`（需要能 import app，即要装依赖）
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

PREFIX: str = "/open-agent-harness"
BASELINE_MIN: int = 58          # 迁移完成时是 62（含 /logout 的 GET+POST），留 4 条余量

# 系统路由（app_fastapi 自己提供，不算业务端点）
SYS = {
    ("GET", "/"), ("GET", "/logout"), ("POST", "/logout"),
    ("GET", "/api/version"), ("GET", "/api/user/currentUser"),
    ("GET", "/test.html"), ("GET", "/test-login"), ("POST", "/test-login"),
    ("GET", "/login"), ("POST", "/login"),
}

# 关键路径抽查：这些接口没了，功能就是缺了
SPOT = [
    ("POST", "/api/chat"),
    ("POST", "/api/chat/upload"),
    ("GET", "/api/sessions"),
    ("GET", "/api/sessions/{}/messages"),
    ("DELETE", "/api/sessions/{}"),
    ("GET", "/api/artifacts/{}/{}"),
    ("GET", "/api/knowledge"), ("POST", "/api/knowledge"), ("DELETE", "/api/knowledge/{}"),
    ("GET", "/api/prefs"), ("DELETE", "/api/prefs/{}"),
    ("GET", "/api/rbac/roles"), ("POST", "/api/users"), ("PATCH", "/api/users/{}"),
    ("GET", "/api/skills"), ("POST", "/api/skills/upload"), ("DELETE", "/api/skills/{}"),
    ("GET", "/api/mcp/servers"), ("POST", "/api/mcp/servers"),
    ("GET", "/api/platform-skills"),
    ("GET", "/api/sandbox/files"), ("POST", "/api/sandbox/mkdir"),
    ("GET", "/api/trace"), ("GET", "/api/audit"),
    ("GET", "/api/warm"), ("POST", "/api/warm"),
]


def norm(p: str) -> str:
    p = re.sub(r"<[^>]+>", "{}", p)
    p = re.sub(r"\{[^}]+\}", "{}", p)
    return p.rstrip("/") or "/"


def actual() -> set:
    """新版 FastAPI 把 include_router 注册成 mount，必须用 openapi() 展开"""
    from src.app_fastapi import app

    out = set()
    for path, ops in (app.openapi().get("paths") or {}).items():
        if path.startswith(PREFIX):
            path = path[len(PREFIX):] or "/"
        for meth in ops:
            m = meth.upper()
            if m in ("HEAD", "OPTIONS", "PARAMETERS"):
                continue
            out.add((m, norm(path)))
    return out


def main() -> int:
    act = actual()
    biz = act - SYS
    missing_spot = [s for s in SPOT if s not in act]

    print(f"业务端点 {len(biz)} 条（基线下限 {BASELINE_MIN}）")
    if missing_spot:
        print(f"\n❌ 关键接口缺失 {len(missing_spot)} 条：")
        for m, p in missing_spot:
            print(f"   {m:6s} {p}")
    ok = len(biz) >= BASELINE_MIN and not missing_spot
    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
