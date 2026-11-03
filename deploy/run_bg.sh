#!/usr/bin/env bash
# 在堡垒机 / SSH 会话里启动**长任务**，让它在会话退出后继续跑
#
# 为什么需要：直接在会话里 `nohup ... &` 不保险 —— 堡垒机断开时整个进程组可能被
# 带走（实测跑完整 test/run_all.sh 走到第 5 步 UI 就被杀了，日志停在半截）。
# `setsid` 让命令进新会话/新进程组，与当前终端彻底脱钩。
#
# 用法：
#   bash deploy/run_bg.sh "bash test/run_all.sh" /tmp/pa_runall.log
#   bash deploy/run_bg.sh "python -m pytest test/api -q" /tmp/pa_api.log
#
# 之后随时：tail -f /tmp/pa_runall.log      看进度
#           ps -o pid,etime,cmd -p <打印出来的 pid>   看还在不在
set -euo pipefail

cmd="${1:-}"
if [ -z "$cmd" ]; then
  echo "用法: bash deploy/run_bg.sh \"<要跑的命令>\" [日志路径]" >&2
  exit 2
fi
log="${2:-/tmp/pa_bg_$(date +%Y%m%d_%H%M%S).log}"

mkdir -p "$(dirname "$log")"
setsid nohup bash -lc "$cmd" >"$log" 2>&1 </dev/null &
pid=$!

echo "started pid=$pid"
echo "log=$log"
echo "tail -f $log    # 跟随输出（会话断开也不影响它继续跑）"
