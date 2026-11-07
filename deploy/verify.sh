#!/bin/bash
# 发版后验收（在**目标机**上跑；本机没有依赖，跑不了 import/单测）
#
#   bash deploy/verify.sh
#   APP_DIR=/opt/open-agent-harness PY=/path/to/python SMOKE=1 bash deploy/verify.sh
#
# 检查：服务/健康/版本 → 未定义名字 → 前端引用 → 前端纯函数 → 单测 → 日志错误
#       SMOKE=1 时再跑一遍 HTTP 端到端冒烟（会真的发起一次 101MB 上传，慢）
# 任何一项不过，退出码 1
set -uo pipefail

APP_DIR=${APP_DIR:-/opt/open-agent-harness}
PY=${PY:-python}
BASE=${BASE:-http://127.0.0.1:5001/open-agent-harness}
SERVICE=${SERVICE:-open-agent-harness}
SMOKE=${SMOKE:-0}

cd "$APP_DIR" || { echo "找不到 APP_DIR=$APP_DIR"; exit 1; }

# 单测会 import 到模块级构造的 LLM 客户端：没有 key 就直接 Missing credentials，
# 看起来像"部署坏了"。验收脚本自己把 .env 载入（install.sh 建的 /opt 目录通常没有 .git）。
if [ -f "$APP_DIR/.env" ]; then set -a; . "$APP_DIR/.env"; set +a; fi

FAIL=0
head() { printf '\n=== %s ===\n' "$*"; }
bad()  { printf '  [FAIL] %s\n' "$*"; FAIL=1; }

head "1) 服务状态"
echo "head=$(git rev-parse --short HEAD 2>/dev/null || echo unknown) dirty=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ') active=$(systemctl is-active "$SERVICE")"
[ "$(systemctl is-active "$SERVICE")" = "active" ] || bad "服务不是 active"

head "2) 健康检查"
curl -s -m 20 "$BASE/api/health"; echo
code=$(curl -s -m 20 -o /dev/null -w '%{http_code}' "$BASE/api/health")
[ "$code" = "200" ] && echo "  health=$code" || bad "health=$code"

head "3) 前端版本"
curl -s -m 20 "$BASE/api/version"; echo

head "4) 未定义名字（NameError 风险）"
$PY test/tools/check_undefined_names.py src config test || bad "存在未定义/未 import 的名字"

head "5) 前端引用 / TDZ"
$PY test/check_refs.py 2>&1 | tail -3 || bad "前端静态检查未通过"

head "6) 前端纯函数单测（node）"
if command -v node >/dev/null 2>&1; then
  JSLOG=/tmp/pa_js.$$
  if node test/js/utils.test.mjs >"$JSLOG" 2>&1; then
    grep -E "tests |pass |fail " "$JSLOG" | sed 's/^/  /'
  else
    tail -12 "$JSLOG"
    bad "前端纯函数单测失败（导航白名单/分页/LRU）"
  fi
  rm -f "$JSLOG"
else
  echo "  跳过（没有 node；装法见 deploy/README.md）"
fi

head "7) 后端单测"
if $PY -c "import langchain_core, fastapi, uvicorn" >/dev/null 2>&1; then
  $PY -m unittest discover -s test/unit -q 2>&1 | tail -4 || bad "单测失败"
else
  echo "  跳过（当前解释器没装依赖）"
fi

head "8) 最近的错误日志（本次重启之后）"
# 日志时间是 UTC。只看最近 10 分钟，避免把几天前的历史 ERROR 当成"本次发版有问题"。
SINCE=$(date -u -d '-10 min' '+%Y-%m-%d %H:%M' 2>/dev/null || echo '')
if [ -n "$SINCE" ]; then
  grep -aE " - ERROR - " logs/app.log 2>/dev/null | awk -v s="$SINCE" 'substr($0,1,16) >= s' | tail -5
else
  grep -aE " - ERROR - " logs/app.log 2>/dev/null | tail -3
fi
echo "(空=本次重启后无错误)"

if [ "$SMOKE" = "1" ]; then
  head "9) HTTP 端到端冒烟"
  BASE="$BASE" bash test/e2e/smoke.sh || bad "冒烟未通过"
fi

head "结果"
if [ $FAIL -eq 0 ]; then echo "✅ 验收通过"; else echo "❌ 有失败项，见上面 [FAIL]"; fi
exit $FAIL
