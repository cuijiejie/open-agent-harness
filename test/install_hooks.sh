#!/bin/bash
# 安装 git 提交前检查钩子（每个 clone 装一次）
#
#   bash test/install_hooks.sh
#
# 之后每次 git commit 都会自动跑：语法 → 前端 → import → 密钥 → 单测
# 跳过单次检查：git commit --no-verify
set -eu
cd "$(git rev-parse --show-toplevel)"
mkdir -p .git/hooks
cp test/hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
echo "✅ 已安装 .git/hooks/pre-commit"
echo "   下次 git commit 自动检查（跳过用 --no-verify）"
