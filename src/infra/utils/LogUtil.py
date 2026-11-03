#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全局日志

日志文件固定落在 <仓库根>/logs/app.log：
以前是 `dirname(__file__) + '/..'` → 实际落到了 src/infra/open_agent_harness.log，
和 gunicorn 写在仓库根的 open_agent_harness.log 重名但不同文件，运维排查时经常看错。
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 本文件在 <repo>/src/infra/utils/LogUtil.py → parents[3] 是仓库根
_LOG_DIR: str = str(Path(__file__).resolve().parents[3] / "logs")
_LOG_FILE: str = os.path.join(_LOG_DIR, "app.log")

log = logging.getLogger('open_agent_harness')
log.setLevel(logging.INFO)
log.propagate = False  # 禁止向 root logger 传播，避免重复输出

if not log.handlers:
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
    except OSError:
        pass
    try:
        rf_handler: logging.Handler = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=10 * 1024 * 1024,  # 10MB per file
            backupCount=5,
            encoding='utf-8',
        )
    except OSError:
        # 日志目录不可写时退化为 stderr，绝不能因为日志把进程搞挂
        rf_handler = logging.StreamHandler()
    rf_handler.setLevel(logging.INFO)

    # 注意：不添加 StreamHandler(stdout) —— gunicorn 配置了 capture_output=True，
    # 会把 worker 的 stdout 重定向到 errorlog（open_agent_harness.log），
    # 若再加 stdout handler 会导致每条日志写文件两遍（重复输出）。

    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    rf_handler.setFormatter(formatter)

    log.addHandler(rf_handler)
