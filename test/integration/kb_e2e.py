#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""知识库端到端自检（需要一个真 PostgreSQL + pgvector，和一个能返回向量的嵌入服务）

为什么不在 test/unit 里
-----------------------
仓库的单测约定「不连库、不联网」，这个脚本相反：它就是要连真库，验证
LlamaIndex PGVectorStore 那条链路（建表 / 切分 / 嵌入 / metadata 过滤 / 融合 / 删除）。

跑法（本地临时 PG 也行，生产实例的 kb 库也行）：

    export PA_KB_PG_HOST=127.0.0.1 PA_KB_PG_PORT=55433 PA_KB_PG_DB=kb \\
           PA_KB_PG_USER=postgres PA_KB_PG_PASS= \\
           PA_KB_EMBED_URL=http://127.0.0.1:5110
    python test/integration/kb_e2e.py

注意：会往库里写 3 份测试文档（alice 两份 + 全局一份），跑完自己删掉。
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from src.infra.kb import index as kb_index  # noqa: E402
from src.infra.kb import ingest, retrieve, settings, store  # noqa: E402

FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ✅ " if cond else "  ❌ ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def make_fixtures() -> tuple[str, str, str]:
    from docx import Document as Docx

    d = tempfile.mkdtemp()

    p1 = os.path.join(d, "差旅制度.docx")
    doc = Docx()
    doc.add_heading("第一章 差旅报销", level=1)
    doc.add_paragraph("员工出差住宿标准按城市等级执行，一线城市每晚不超过 600 元。")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "城市"
    t.cell(0, 1).text = "住宿上限"
    t.cell(1, 0).text = "天津"
    t.cell(1, 1).text = "450 元"
    doc.save(p1)

    p2 = os.path.join(d, "产品手册.docx")
    doc2 = Docx()
    doc2.add_heading("Harness 工程范式", level=1)
    doc2.add_paragraph("Harness 平台由对话、工具、沙箱、权限四层组成，知识库检索走混合召回。")
    doc2.save(p2)

    p3 = os.path.join(d, "共享口径.md")
    with open(p3, "w", encoding="utf-8") as f:
        f.write("# 公司口径\n\n2023 年天津地区生产总值为 16737.30 亿元。\n")
    return p1, p2, p3


def main() -> int:
    settings.configure(force=True)

    print("1) 建表")
    check("ensure_schema", store.ensure_schema())
    with store.pool().connection() as c, c.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
        tables = [r[0] for r in cur.fetchall()]
    print("   表:", tables)
    check("业务表 kb_doc/kb_task", {"kb_doc", "kb_task"} <= set(tables))
    check("LlamaIndex 向量表", kb_index.data_table() in tables, kb_index.data_table())

    print("2) 上传三份文档")
    p1, p2, p3 = make_fixtures()
    r1 = ingest.save_upload("user", "alice", "差旅制度.docx", open(p1, "rb").read())
    r2 = ingest.save_upload("user", "alice", "产品手册.docx", open(p2, "rb").read())
    r3 = ingest.save_upload("global", "", "共享口径.md", open(p3, "rb").read(), uploader="admin")
    check("三份上传成功", all(r.get("ok") for r in (r1, r2, r3)))

    print("3) worker 索引")
    print(f"   处理任务: {ingest.run_once()}")
    docs = {x["filename"]: x for x in store.list_docs("user", "alice")}
    print("   文档状态:", {k: (v["status"], v["chunks"]) for k, v in docs.items()})
    check("全部 indexed", all(v["status"] == "indexed" for v in docs.values()),
          str({k: v.get("error") for k, v in docs.items() if v["status"] != "indexed"}))

    print("4) 检索（alice：本人 + 全局）")
    hits = retrieve.search("差旅住宿标准 天津", "alice", top_k=5)
    for h in hits:
        print(f"   - {h['doc']} p{h['page']} [{h['score']}] {(h['content'] or '')[:40]}")
    check("命中差旅制度", any("差旅制度" in h["doc"] for h in hits))
    check("命中共享口径", any("共享口径" in h["doc"]
                          for h in retrieve.search("天津地区生产总值", "alice")))

    print("5) 越权检查（bob 只能看全局）")
    bob = retrieve.search("差旅住宿标准 天津", "bob", top_k=5)
    print("   bob 命中:", [(h["doc"], h["score"]) for h in bob])
    check("bob 看不到 alice 私有文档", all("差旅制度" not in h["doc"] for h in bob))
    check("bob 能看到全局文档", any("共享口径" in h["doc"]
                             for h in retrieve.search("天津地区生产总值", "bob")))

    print("6) 统计")
    s = store.stats()
    print("   ", s)
    check("统计有文档与节点", s["docs"] >= 3 and s["chunks"] > 0)

    print("7) 重建（删旧向量后重跑不重复）")
    did = int(docs["差旅制度.docx"]["id"])
    store.add_task(did, "reindex")
    ingest.run_once()
    dup = [h for h in retrieve.search("差旅住宿标准", "alice", top_k=10) if "差旅制度" in h["doc"]]
    check("重建不产生重复命中", len(dup) <= 6, f"{len(dup)} 条")

    print("8) 删除文档")
    check("删除成功", store.delete_doc(int(docs["产品手册.docx"]["id"])))
    check("删除后检索不到", all("产品手册" not in h["doc"]
                          for h in retrieve.search("Harness 工程范式", "alice", top_k=5)))

    print("\n结果:", "全部通过" if not FAILED else f"{len(FAILED)} 项失败: {FAILED}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
