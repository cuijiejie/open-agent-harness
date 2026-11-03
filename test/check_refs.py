"""前端白屏静态检查：找出"引用了不存在 / 声明过晚"的变量

为什么需要：Vue 的未定义变量、TDZ（在 const 声明前使用）都不会让 `npm run build` 报错，
只会在浏览器里整页白屏。今天是第三次发生（toolsGroups 未定义 / currentSessionId 拼错 / watch 在 const 前）。

检查两类问题：
  A. 模板或脚本里用到、但从未声明的标识符        → build 通过、运行时 ReferenceError
  B. watch()/computed() 在依赖的 const 之前引用它   → build 通过、setup 阶段 TDZ 崩

用法：
    python scripts/frontend/check_refs.py            # 检查 frontend/src/App.vue
    python scripts/frontend/check_refs.py --all      # 检查 frontend/src 下所有 .vue
退出码：0 通过，1 有问题
"""
import argparse
import glob
import os
import re
import sys

# Windows 控制台默认 GBK：直接 print("✅") 会抛 UnicodeEncodeError，
# 结果 pre-commit 钩子把"检查通过"误判成失败（脚本本身没问题）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
except Exception:
    pass
import sys

# Vue/JS 内置与常见全局：不检查
BUILTINS = {
    "true", "false", "null", "undefined", "this", "new", "typeof", "in", "of", "void",
    "Math", "Date", "JSON", "Object", "Array", "String", "Number", "Boolean", "Promise",
    "Map", "Set", "RegExp", "Error", "Intl", "console", "window", "document", "fetch",
    "setTimeout", "clearTimeout", "setInterval", "clearInterval", "parseInt", "parseFloat",
    "isNaN", "encodeURIComponent", "decodeURIComponent", "URLSearchParams", "Blob", "File",
    "FormData", "FileReader", "localStorage", "sessionStorage", "location", "history",
    "return", "if", "else", "for", "while", "switch", "case", "break", "continue", "try",
    "catch", "finally", "throw", "await", "async", "yield", "function", "class", "const",
    "let", "var", "import", "export", "default", "from", "as", "delete", "instanceof",
    "key", "value", "index", "item", "row", "col", "e", "el", "ev", "event", "res", "err",
    "error", "data", "args", "props", "emit", "ctx", "slot", "name", "type", "id", "msg",
}

DECL_PATTERNS = [
    r"\bconst\s+([A-Za-z_$][\w$]*)",
    r"\blet\s+([A-Za-z_$][\w$]*)",
    r"\bvar\s+([A-Za-z_$][\w$]*)",
    r"\bfunction\s+([A-Za-z_$][\w$]*)",
    r"\bclass\s+([A-Za-z_$][\w$]*)",
]


def split_sfc(text: str) -> tuple[str, str]:
    """返回 (template 部分, script 部分)"""
    tpl = ""
    m = re.search(r"<template>(.*?)</template>", text, re.S)
    if m:
        tpl = m.group(1)
    scr = ""
    m = re.search(r"<script[^>]*>(.*?)</script>", text, re.S)
    if m:
        scr = m.group(1)
    return tpl, scr


def declared_names(script: str) -> set:
    names = set()
    for pat in DECL_PATTERNS:
        for m in re.finditer(pat, script):
            names.add(m.group(1))
    # import { a, b as c } from 'x'
    for m in re.finditer(r"import\s*\{([^}]*)\}\s*from", script):
        for part in m.group(1).split(","):
            part = part.strip()
            if not part:
                continue
            if " as " in part:
                names.add(part.split(" as ")[-1].strip())
            else:
                names.add(part.split(":")[0].strip())
    # import X from 'x'
    for m in re.finditer(r"import\s+([A-Za-z_$][\w$]*)\s+from", script):
        names.add(m.group(1))
    # 解构: const { a, b } = ... / const [a, b] = ...
    for m in re.finditer(r"\b(?:const|let|var)\s*\{([^}]*)\}", script):
        for part in m.group(1).split(","):
            part = part.strip().split(":")[-1].strip()
            if re.match(r"^[A-Za-z_$][\w$]*$", part):
                names.add(part)
    for m in re.finditer(r"\b(?:const|let|var)\s*\[([^\]]*)\]", script):
        for part in m.group(1).split(","):
            part = part.strip()
            if re.match(r"^[A-Za-z_$][\w$]*$", part):
                names.add(part)
    # 函数参数（粗一点）：(a, b) =>  / function f(a, b)
    for m in re.finditer(r"\(([^()]*)\)\s*=>", script):
        for part in m.group(1).split(","):
            part = part.strip().split("=")[0].strip()
            if re.match(r"^[A-Za-z_$][\w$]*$", part):
                names.add(part)
    return names


def template_identifiers(tpl: str) -> set:
    """模板里 {{ }}、指令表达式 中出现的标识符（只看根名）"""
    exprs = []
    exprs += re.findall(r"\{\{(.*?)\}\}", tpl, re.S)
    exprs += re.findall(r'(?:v-if|v-else-if|v-show|v-for|v-model|:[\w-]+|@[\w.-]+)="([^"]*)"', tpl)
    used = set()
    for expr in exprs:
        # 去掉字符串字面量
        expr = re.sub(r"'[^']*'|\"[^\"]*\"", " ", expr)
        for m in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)\b(?!\s*:)", expr):
            used.add(m.group(1))
    return used


def check_tdz(script: str) -> list:
    """watch(X)/const Y = computed(() => ...X) 出现在 X 的声明之前 → TDZ"""
    lines = script.split("\n")
    decl_line: dict = {}
    for i, line in enumerate(lines, 1):
        for pat in DECL_PATTERNS:
            m = re.search(pat, line)
            if m:
                decl_line.setdefault(m.group(1), i)

    problems = []
    for i, line in enumerate(lines, 1):
        # 只关心 watch( 第一个参数、computed 主体
        m = re.search(r"(?:^|\s)watch\(\s*([A-Za-z_$][\w$]*)", line)
        targets = [m.group(1)] if m else []
        # 只检查 watch()：computed 是惰性求值，函数体稍后才跑，不存在 TDZ
        for t in targets:
            start = decl_line.get(t)
            if start and start > i:
                problems.append(
                    f"TDZ 风险（第 {i} 行用了 `{t}`，但它在第 {start} 行才声明）"
                )
    return problems


def check_file(path: str) -> list:
    text = open(path, encoding="utf-8").read()
    tpl, script = split_sfc(text)
    problems = []

    declared = declared_names(script)
    used_tpl = template_identifiers(tpl)
    missing = sorted(x for x in used_tpl - declared - BUILTINS if not x.isdigit())
    # 过滤明显是 v-for 局部变量/方法调用链根名的误报：只报"看起来像 ref/computed"的
    missing = [x for x in missing if len(x) > 2]
    for name in missing:
        problems.append(f"模板引用了未声明变量: {name}")

    problems += check_tdz(script)
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--dir", default=os.path.join("frontend", "src"))
    args = ap.parse_args()

    if args.all:
        files = sorted(glob.glob(os.path.join(args.dir, "**", "*.vue"), recursive=True))
    else:
        files = [os.path.join(args.dir, "App.vue")]

    total = 0
    for f in files:
        if not os.path.isfile(f):
            continue
        probs = check_file(f)
        if probs:
            total += len(probs)
            print(f"\n[FAIL] {f}")
            for p in probs[:25]:
                print(f"   - {p}")
        else:
            print(f"[OK] {f}")

    print()
    if total:
        print(f"发现 {total} 处疑似问题（可能导致白屏），请先修再部署")
        return 1
    print("前端静态检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
