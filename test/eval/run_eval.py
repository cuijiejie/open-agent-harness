#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""知识库检索冒烟评测（对着已部署实例跑）

为什么要有它：检索参数（切块、余弦下限、RRF 权重、重排开关）改动后，
"看着还行"和"真的还行"差很远。这一组用例固化了两类错误：

  - 该命中的没命中（相关性变差、阈值调过头）
  - 不该命中的硬凑（库里没有相关内容却返回一堆噪声）

用法：
    python test/eval/run_eval.py --base http://127.0.0.1:5002/open-agent-harness
    python test/eval/run_eval.py --token <会话 cookie> --verbose
    PA_EVAL_TOKEN=... python test/eval/run_eval.py

会话 cookie 默认读 /root/tok.txt（部署机上的管理员会话）；也可用 --token 直接给。
退出码：0 全通过；1 有失败；2 环境问题（连不上 / 没令牌）。

判定字段：
  expect = has_hits  → 至少 1 条，且第 1 条来自 expect_docs（列表内任一）
  expect = no_hits   → 必须 0 条
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))


def load_cases(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    ap = argparse.ArgumentParser(description="知识库检索冒烟评测")
    ap.add_argument("--base", default=os.environ.get("PA_EVAL_BASE", "http://127.0.0.1:5002/open-agent-harness"))
    ap.add_argument("--token", default=os.environ.get("PA_EVAL_TOKEN", ""))
    ap.add_argument("--token-file", default="/root/tok.txt")
    ap.add_argument("--cases", default=os.path.join(HERE, "cases.json"))
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    token: str = args.token.strip()
    if not token and os.path.isfile(args.token_file):
        token = open(args.token_file).read().strip()
    if not token:
        print(f"缺少会话令牌：用 --token 或 PA_EVAL_TOKEN 提供（默认尝试 {args.token_file}）")
        return 2

    s = requests.Session()
    s.cookies.set("session", token)
    base: str = args.base.rstrip("/")

    try:
        h = s.get(f"{base}/api/health", timeout=15)
    except Exception as e:
        print(f"连不上实例 {base}：{e}")
        return 2
    if h.status_code != 200:
        print(f"健康检查失败：HTTP {h.status_code}")
        return 2

    cases = load_cases(args.cases)
    rows: list[dict] = []
    failed: int = 0

    for c in cases:
        q: str = str(c.get("query") or "")
        expect: str = str(c.get("expect") or "has_hits")
        want_docs: list[str] = [str(x) for x in (c.get("expect_docs") or [])]
        try:
            r = s.get(f"{base}/api/kb/search", params={"q": q, "limit": args.top_k}, timeout=180)
            items = (r.json().get("items") or []) if r.status_code == 200 else []
        except Exception as e:
            items = []
            r = None
            print(f"  查询异常：{e}")

        n: int = len(items)
        top: dict = items[0] if items else {}
        top_doc: str = str(top.get("doc") or "")
        top_score: float = float(top.get("score") or 0)

        if expect == "no_hits":
            ok: bool = n == 0
            reason: str = "" if ok else f"期望 0 条，实际 {n} 条"
        else:
            doc_ok: bool = (not want_docs) or any(w in top_doc for w in want_docs)
            ok = n > 0 and doc_ok
            reason = "" if ok else (f"0 条命中" if n == 0 else f"首条来自「{top_doc}」，期望 {want_docs}")

        if not ok:
            failed += 1
        rows.append({"id": c.get("id"), "query": q, "expect": expect, "hits": n,
                     "top_doc": top_doc, "top_score": round(top_score, 4), "ok": ok, "reason": reason})
        print(f"{'PASS' if ok else 'FAIL'} [{c.get('id')}] {q[:34]:36} 命中={n} 首条={top_doc[:26] or '—'}"
              + (f"  ← {reason}" if reason else ""))
        if args.verbose:
            for it in items[:3]:
                print(f"        {it.get('score')} {str(it.get('doc'))[:30]} | {str(it.get('content'))[:60]!r}")

    total: int = len(rows)
    print(f"\n{total - failed}/{total} 通过" + ("" if failed == 0 else f"，{failed} 条失败"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
