#!/usr/bin/env bash
# =============================================================================
# Open Agent Harness · 一键安装/升级
#
#   sudo bash deploy/install.sh                  # 默认装到 /opt/open-agent-harness，端口 5001
#   sudo APP_DIR=/srv/agent PORT=8080 bash deploy/install.sh
#
# 幂等：重复执行 = 升级（重装依赖、重建前端、重启服务）
# 前置：python3.12、node 18+、npm、MySQL（PG/Redis/沙箱可选）
# =============================================================================
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/open-agent-harness}
PORT=${PORT:-5001}
SERVICE=${SERVICE:-open-agent-harness}
SKIP_FRONTEND=${SKIP_FRONTEND:-0}

# 找 Python：优先 3.12（依赖按 3.12 锁的），也会去常见安装位置找
find_python() {
  for c in python3.12 /usr/bin/python3.12 /usr/local/bin/python3.12 \
           /data1/miniforge3/envs/*/bin/python /opt/conda/envs/*/bin/python \
           python3.13 python3.11 python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null && { echo "$c"; return; }
  done
}
PY=${PYTHON:-$(find_python)}
[ -n "${PY:-}" ] || { echo "找不到 Python 3.11+，请先安装（推荐 3.12）" >&2; exit 1; }

blue() { printf "\n\033[1;34m==> %s\033[0m\n" "$*"; }
ok()   { printf "    \033[1;32m✓\033[0m %s\n" "$*"; }
warn() { printf "    \033[1;33m!\033[0m %s\n" "$*"; }
die()  { printf "\n\033[1;31m[失败]\033[0m %s\n" "$*" >&2; exit 1; }

need() { command -v "$1" >/dev/null 2>&1 || die "缺少 $1，请先安装：$2"; }

blue "0/7 环境检查"
need "$PY" "$PY（建议 3.12）"
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), "需要 Python 3.11+"' || die "Python 版本过低"
ok "$($PY --version)（$PY）"
if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
  if [ "${ALLOW_OTHER_PYTHON:-0}" = "1" ]; then
    warn "用的不是 3.12（$("$PY" -V 2>&1)）：依赖按 3.12 锁的，装不上就换 3.12"
  else
    cat <<'EOS'
[失败] 需要 Python 3.12（依赖按 3.12 锁的，3.13/3.14 上部分包没有预编译轮子，会现场编译或直接失败）

  安装 3.12：  sudo apt install -y python3.12 python3.12-venv
  已有 3.12：  PYTHON=/usr/bin/python3.12 sudo -E bash deploy/install.sh
  想强行用其他版本：ALLOW_OTHER_PYTHON=1 bash deploy/install.sh
EOS
    exit 1
  fi
fi

if [ "$SKIP_FRONTEND" != "1" ]; then
  need node "Node.js 18+"
  need npm "npm"
  ok "node $(node -v) / npm $(npm -v)"
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "$SRC_DIR" != "$APP_DIR" ]; then
  blue "1/7 同步代码到 $APP_DIR"
  mkdir -p "$APP_DIR"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
      --exclude '__pycache__' --exclude 'logs' --exclude '.env' \
      "$SRC_DIR"/ "$APP_DIR"/
  else
    cp -a "$SRC_DIR"/. "$APP_DIR"/
  fi
  ok "已同步（保留 $APP_DIR/.env 与 logs/）"
else
  blue "1/7 代码目录即 $APP_DIR，跳过同步"
fi

cd "$APP_DIR"

blue "2/7 Python 虚拟环境 + 依赖"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -r requirements.txt
ok "依赖就绪（$(.venv/bin/python -V)）"

blue "3/7 前端构建"
if [ "$SKIP_FRONTEND" = "1" ]; then
  warn "按 SKIP_FRONTEND=1 跳过；确认 frontend/dist/index.html 已存在"
  [ -f frontend/dist/index.html ] || die "frontend/dist/index.html 不存在，去掉 SKIP_FRONTEND 重跑"
else
  ( cd frontend && npm ci --silent && npm run build >/dev/null )
  [ -f frontend/dist/index.html ] || die "前端构建失败：frontend/dist/index.html 不存在"
  ok "产物 frontend/dist"
fi

blue "4/7 生成 .env（密钥只落在服务器本地，权限 600）"
if [ -f .env ]; then
  ok ".env 已存在，保留不动"
  SECRET=$(grep -E '^PA_APP_SECRET_KEY=' .env | cut -d= -f2- || true)
  [ -n "$SECRET" ] || warn "PA_APP_SECRET_KEY 为空，登录态会随机失效"
else
  SECRET=$("$PY" -c 'import secrets;print(secrets.token_hex(32))')
  MYSQL_PASS=${PA_DB_MYSQL_PASS:-}
  MYSQL_HOST=${PA_DB_MYSQL_HOST:-127.0.0.1}
  MYSQL_USER=${PA_DB_MYSQL_USER:-agent}
  MYSQL_DB=${PA_DB_MYSQL_DB:-open_agent_harness}
  LLM_KEY=${PA_LLM_DEEPSEEK_API_KEY:-}
  {
    echo "# 由 deploy/install.sh 生成 $(date '+%F %T')"
    echo "PA_DB_MYSQL_HOST=$MYSQL_HOST"
    echo "PA_DB_MYSQL_PORT=${PA_DB_MYSQL_PORT:-3306}"
    echo "PA_DB_MYSQL_USER=$MYSQL_USER"
    echo "PA_DB_MYSQL_PASS=$MYSQL_PASS"
    echo "PA_DB_MYSQL_DB=$MYSQL_DB"
    echo "PA_LLM_DEEPSEEK_API_KEY=$LLM_KEY"
    echo "PA_APP_SECRET_KEY=$SECRET"
    echo "PA_APP_TEST_LOGIN_ENABLED=${PA_APP_TEST_LOGIN_ENABLED:-false}"
    echo "PA_OPENSHELL_ENABLED=${PA_OPENSHELL_ENABLED:-false}"
  } > .env
  ok "已生成 .env"
  [ -n "$MYSQL_PASS" ] || warn "PA_DB_MYSQL_PASS 为空 —— 请编辑 $APP_DIR/.env 填上数据库口令"
  [ -n "$LLM_KEY" ]    || warn "PA_LLM_DEEPSEEK_API_KEY 为空 —— 不填则对话不可用"
fi
chmod 600 .env
mkdir -p logs

blue "5/7 数据库连通性自检"
.venv/bin/python - <<'PY' || warn "数据库连不上：先建库建账号（见 docs/部署说明.md §3），再重跑本脚本"
import os, sys
sys.path.insert(0, ".")
for line in open(".env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k, v)
from config.Configs import cfg
import pymysql
pymysql.connect(host=cfg.mysqlHost, port=int(cfg.mysqlPort), user=cfg.mysqlUser,
                password=cfg.mysqlPass, database=cfg.mysqlDB, connect_timeout=5).close()
print("    数据库连接正常:", cfg.mysqlHost, cfg.mysqlDB)
PY

blue "6/7 安装 systemd 服务"
if [ ! -w /etc/systemd/system ] || [ "$(id -u)" != "0" ]; then
  warn "非 root：跳过 systemd，改为前台试跑"
  echo "    手动启动：cd $APP_DIR && PYTHONPATH=. .venv/bin/python -m uvicorn src.app_fastapi:app --host 0.0.0.0 --port $PORT --workers 4"
else
  sed -e "s#/opt/open-agent-harness#$APP_DIR#g" -e "s#0.0.0.0:5001#0.0.0.0:$PORT#g" \
    deploy/open-agent-harness.service > "/etc/systemd/system/$SERVICE.service"
  systemctl daemon-reload
  systemctl enable "$SERVICE" >/dev/null 2>&1 || true
  systemctl restart "$SERVICE"
  ok "systemctl restart $SERVICE"
fi

blue "7/7 探活"
URL="http://127.0.0.1:$PORT/open-agent-harness/"
for i in $(seq 1 15); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "$URL" 2>/dev/null || echo 000)
  case "$code" in
    200|302) ok "服务已就绪（HTTP $code）"; break ;;
    *) [ "$i" = "15" ] && die "15 次探活失败（最后状态 $code），看日志：tail -50 $APP_DIR/logs/app.log"; sleep 2 ;;
  esac
done

cat <<EOF

────────────────────────────────────────────────────────────
部署完成

  访问地址   http://<服务器IP>:$PORT/open-agent-harness/
  登录       用户名 + 口令（未设口令 = 免密，见 config/configs.cfg [app] login_password）
  首次进入   第一个登录的用户不会自动是管理员：
             在数据库里给他 admin 角色，见 docs/部署说明.md §7「设管理员」
  日志       $APP_DIR/logs/app.log
  服务       systemctl status $SERVICE

下一步：把 [app] test_login_enabled 改成 false（对外开放前必做）
────────────────────────────────────────────────────────────
EOF
