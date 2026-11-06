#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""路由契约检查：FastAPI 路由表必须覆盖现有 Flask controller 的每一个端点

用途：Flask → FastAPI 迁移的**验收基线**。少一条就是把功能搬丢了，
多出来的必须是系统路由（已登记在 SYS_EXTRA）。
不依赖 pytest，直接跑：`python test/unit/route_contract.py`
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# 系统路由：由 app_fastapi 自己提供，controller 里也有同名（如 /logout），
# 或属于前端静态/探针，不计入"多出"
PREFIX: str = "/open-agent-harness"

SYS_EXTRA = {
    ("GET", "/"), ("GET", "/logout"), ("POST", "/logout"),
    ("GET", "/api/version"), ("GET", "/api/user/currentUser"),
    ("GET", "/test.html"), ("GET", "/test-login"), ("POST", "/test-login"),
}


def norm(p: str) -> str:
    p = re.sub(r"<[^>]+>", "{}", p)
    p = re.sub(r"\{[^}]+\}", "{}", p)
    return p.rstrip("/") or "/"


def expected() -> set:
    """从 controller 源码抽（不 import Flask，避免副作用）"""
    out: set = set()
    d = os.path.join(ROOT, "src", "controller")
    for f in sorted(os.listdir(d)):
        if not f.endswith(".py") or f == "__init__.py":
            continue
        src = open(os.path.join(d, f), encoding="utf-8").read()
        for m in re.finditer(r"@\w+\.route\(\s*['\"]([^'\"]+)['\"]"
                             r"(?:\s*,\s*methods\s*=\s*\[([^\]]*)\])?", src):
            methods = re.findall(r"['\"](\w+)['\"]", m.group(2) or "['GET']") or ["GET"]
            for meth in methods:
                out.add((meth.upper(), norm(m.group(1))))
    return out


def actual() -> set:
    """从 OpenAPI schema 取路由表

    不能直接扫 `app.routes`：新版 FastAPI（0.16x / starlette 1.x）把 `include_router`
    注册成 mount，端点不再平铺在 `app.routes` 里（扫出来只有系统路由，会误报"全部缺失"）。
    `app.openapi()` 会把 mount 里的路由一并展开，且路径参数已经是 `{name}` 形式。
    """
    from src.app_fastapi import app

    schema: dict = app.openapi()
    out: set = set()
    for path, ops in (schema.get("paths") or {}).items():
        for meth in ops.keys():
            m = meth.upper()
            if m in ("HEAD", "OPTIONS", "PARAMETERS"):
                continue
            out.add((m, norm(path)))
    return out


def main() -> int:
    exp, act = expected(), actual()
    missing = sorted(exp - act)
    extra = sorted(act - exp - SYS_EXTRA)

    print(f"controller 端点 {len(exp)} 个 | FastAPI 端点 {len(act)} 个")
    if missing:
        print(f"\n❌ 缺失 {len(missing)} 条（迁移漏搬）：")
        for meth, path in missing:
            print(f"   {meth:6s} {path}")
    if extra:
        print(f"\n⚠️  多出 {len(extra)} 条（未登记的系统路由，请确认）：")
        for meth, path in extra:
            print(f"   {meth:6s} {path}")

    ok = not missing
    print("\nRESULT:", "PASS" if ok else f"FAIL（缺 {len(missing)} 条）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
