#!/usr/bin/env bash
# 全量自检：后端单元测试 → 前端纯函数测试 → 静态检查
#
#   bash test/run_all.sh
#
# 退出码：任何一层失败 → 1
#
# 说明：仓库里只带「不需要外部依赖」的测试（不连库、不联网、不起沙箱），
# 这样任何人 clone 下来就能验证代码是好的。集成测试请对着自己部署的实例做。
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

PY=${PY:-python}

# 单测不连真实模型：给个占位 key，避免 OpenAI 客户端在构造时报缺凭据
export PA_LLM_DEEPSEEK_API_KEY=${PA_LLM_DEEPSEEK_API_KEY:-sk-placeholder-for-unit-tests}
export PA_APP_SECRET_KEY=${PA_APP_SECRET_KEY:-unit-test-secret}
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

FAILED=""
mark_fail() { FAILED="$FAILED $1"; }

head() { printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }

# ── 1. 后端单元测试 ────────────────────────────────────────────
# -t 必须是 test/unit 本身：仓库根不是包（没有 test/__init__.py），
# 用 -t . 会想 import test.unit，直接 ModuleNotFoundError。
head "1/3 后端单元测试（unittest）"
if $PY -m unittest discover -s test/unit -t test/unit -q 2>&1 | tail -3; then
  echo "  -> OK"
else
  echo "  -> FAIL"; mark_fail unit
fi

# ── 2. 前端纯函数测试 ──────────────────────────────────────────
head "2/3 前端纯函数测试（node）"
if command -v node >/dev/null 2>&1; then
  if node test/js/utils.test.mjs >/dev/null 2>&1; then
    echo "  -> OK"
  else
    echo "  -> FAIL"; node test/js/utils.test.mjs | tail -5; mark_fail js
  fi
else
  echo "  -> SKIP（没装 node）"
fi

# ── 3. 静态检查 ────────────────────────────────────────────────
head "3/3 静态检查（未定义名字 / 前端引用 / 路由契约）"
if $PY test/tools/check_undefined_names.py src config test >/dev/null 2>&1; then
  echo "  未定义名字: OK"
else
  echo "  未定义名字: FAIL"; $PY test/tools/check_undefined_names.py src config test | tail -5; mark_fail names
fi
if $PY test/check_refs.py >/dev/null 2>&1; then
  echo "  前端引用: OK"
else
  echo "  前端引用: FAIL"; mark_fail refs
fi
# 路由契约：文件名不是 test_*.py，unittest discover 收不到，只能显式跑
if $PY test/unit/route_contract.py >/dev/null 2>&1; then
  echo "  路由契约: OK"
else
  echo "  路由契约: FAIL"; $PY test/unit/route_contract.py 2>&1 | tail -5; mark_fail routes
fi

# ── 汇总 ───────────────────────────────────────────────────────
if [ -z "$FAILED" ]; then
  printf '\n\033[32m全部通过\033[0m\n'
  exit 0
fi
printf '\n\033[31m失败项:%s\033[0m\n' "$FAILED"
exit 1
