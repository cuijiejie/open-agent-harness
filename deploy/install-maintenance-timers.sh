#!/usr/bin/env bash
# 安装/更新「一个任务一个 timer」的维护定时器
#   pa-logs           每天 03:00 UTC  日志切分 + 清理
#   pa-artifacts      每天 03:10 UTC  交付物 90 天清理
#   pa-sandbox-idle   每天 03:20 UTC  闲置沙箱 stop（只停不删）
#   pa-sandbox-orphan 每天 03:30 UTC  孤儿沙箱 delete
#
# 用法：在服务器上执行  bash deploy/install-maintenance-timers.sh
set -euo pipefail
cd "$(dirname "$0")"

APP_DIR=${APP_DIR:-/opt/open-agent-harness}
# 解释器：默认沿用 install.sh 记在 .env 里的选择（conda 或 venv），找不到再退回 .venv
if [ -z "${PY_BIN:-}" ]; then
  if [ -f "$APP_DIR/.env" ] && grep -q '^PA_INSTALL_USE_CONDA=1' "$APP_DIR/.env"; then
    _prefix=$(grep -E '^PA_INSTALL_CONDA_PREFIX=' "$APP_DIR/.env" | tail -1 | cut -d= -f2-)
    _env=$(grep -E '^PA_INSTALL_CONDA_ENV=' "$APP_DIR/.env" | tail -1 | cut -d= -f2-)
    PY_BIN="${_prefix:-/opt/miniforge3}/envs/${_env:-oah}/bin/python"
  else
    PY_BIN="$APP_DIR/.venv/bin/python"
  fi
fi
[ -x "$PY_BIN" ] || { echo "找不到 python: $PY_BIN（可用 PY_BIN=... 指定）" >&2; exit 1; }
echo "==> 解释器: $PY_BIN"

UNITS=(pa-logs.service pa-logs.timer
       pa-artifacts.service pa-artifacts.timer
       pa-sandbox-idle.service pa-sandbox-idle.timer
       pa-sandbox-orphan.service pa-sandbox-orphan.timer)

echo "==> 复制 unit 文件到 /etc/systemd/system/（替换解释器与安装目录）"
for u in "${UNITS[@]}"; do
  sed -e "s#/opt/open-agent-harness#$APP_DIR#g" \
      -e "s#/opt/open-agent-harness/.venv/bin/python#$PY_BIN#g" \
      "$u" > "/etc/systemd/system/$u"
done

echo "==> 停用旧的「一锅端」定时器（如果存在）"
systemctl disable --now pa-maintenance.timer 2>/dev/null || true

echo "==> 重新加载 + 启用"
systemctl daemon-reload
systemctl enable --now pa-logs.timer pa-artifacts.timer \
                       pa-sandbox-idle.timer pa-sandbox-orphan.timer

echo "==> 当前定时器"
systemctl list-timers 'pa-*' --no-pager || true
