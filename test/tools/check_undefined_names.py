#!/usr/bin/env python3
"""静态检查：函数体里引用了「模块级不存在、也不是内置」的名字（NameError 风险）

为什么需要
----------
`python -m py_compile` 只查语法，不查名字。改函数体时忘了 import（或删了 import
留着调用）要靠**跑起来**才会炸，往往炸在用户请求路径上。这个检查用 symtable 看
每个作用域的全局引用：如果这个名字既不在模块级标识符里、也不是内置 → 报出来。

它能抓到的典型问题（本项目真实踩过）：
    sandbox.py 里用了 `time.time()`，但文件没 `import time` → 所有沙箱调用 NameError
它抓不到：拼错的属性名（obj.attr）、按字符串取 key 的错、逻辑错。

用法
----
    python test/tools/check_undefined_names.py src config test
    python test/tools/check_undefined_names.py src/chat/stream.py
退出码：0 干净，1 有问题。

注意：`__file__` / `__name__` 这类模块级 dunder 是运行时注入的，不在 symtable 的
模块标识符里，已显式跳过（否则每个用 `__file__` 的函数都会误报）。
"""
from __future__ import annotations

import builtins
import os
import sys
import symtable

# 运行时由解释器注入的模块级名字，symtable 看不到
_DUNDERS = {
    "__file__", "__name__", "__doc__", "__spec__", "__package__", "__loader__",
    "__builtins__", "__annotations__", "__cached__", "__dict__",
}


def check_file(path: str) -> list[tuple[str, str, str]]:
    src = open(path, encoding="utf-8").read()
    st = symtable.symtable(src, path, "exec")
    mod_names = set(st.get_identifiers())
    problems: list[tuple[str, str, str]] = []

    def walk(tbl) -> None:
        for child in tbl.get_children():
            walk(child)
        fname = tbl.get_name()
        for sym in tbl.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced():
                continue
            if sym.is_parameter() or sym.is_local() or sym.is_imported():
                continue
            if not sym.is_global():
                continue
            if name in mod_names or name in _DUNDERS or hasattr(builtins, name):
                continue
            problems.append((path, fname, name))

    walk(st)
    return problems


def iter_py(target: str):
    if os.path.isfile(target):
        yield target
        return
    for dp, dns, fns in os.walk(target):
        dns[:] = [d for d in dns if d not in ("__pycache__", "node_modules", ".git", ".venv")]
        for fn in fns:
            if fn.endswith(".py"):
                yield os.path.join(dp, fn)


def main(argv: list[str]) -> int:
    targets = argv or ["src"]
    bad = 0
    for t in targets:
        for f in iter_py(t):
            try:
                for path, fn, name in check_file(f):
                    bad += 1
                    print(f"{path}:{fn}: 未定义的名字 {name!r}")
            except SyntaxError as e:
                bad += 1
                print(f"{f}: 语法错误 {e}")
    print(f"PROBLEMS = {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
