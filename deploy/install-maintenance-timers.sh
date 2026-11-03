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

UNITS=(pa-logs.service pa-logs.timer
       pa-artifacts.service pa-artifacts.timer
       pa-sandbox-idle.service pa-sandbox-idle.timer
       pa-sandbox-orphan.service pa-sandbox-orphan.timer)

echo "==> 复制 unit 文件到 /etc/systemd/system/"
cp "${UNITS[@]}" /etc/systemd/system/

echo "==> 停用旧的「一锅端」定时器（如果存在）"
systemctl disable --now pa-maintenance.timer 2>/dev/null || true

echo "==> 重新加载 + 启用"
systemctl daemon-reload
systemctl enable --now pa-logs.timer pa-artifacts.timer \
                       pa-sandbox-idle.timer pa-sandbox-orphan.timer

echo "==> 当前定时器"
systemctl list-timers 'pa-*' --no-pager || true
