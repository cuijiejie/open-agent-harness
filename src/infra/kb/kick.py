"""索引触发：上传/重建后立刻起一个后台 worker

为什么不当场跑
--------------
索引要解析、切分、调嵌入服务，扫描件还要 OCR（实测约 20 秒/页）。
在请求线程里跑会把 uvicorn 的 worker 占住几十秒到几十分钟，
上传接口超时、同时段别人的对话也受影响。

为什么不用定时任务
------------------
定时任务最早是"夜里批量"的设计，代价是上传后最长要等到凌晨才能搜到。
现在改成：上传成功立即踢一脚，worker 在**独立进程**里跑，
请求立刻返回；页面自己轮询状态（排队中 → 索引中 → 可用）。
没有定时器，靠两处兜底：
  1. 服务启动时如果有 pending 任务，补跑一次（部署/重启后不会漏）
  2. 上传/重建本身就是触发器，卡住的文档用户点「重建」即可
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from src.infra.utils.LogUtil import log

_UNIT: str = "pa-kb-index-now"


def _root() -> str:
    # src/infra/kb/kick.py → 仓库根目录
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def kick(reason: str = "") -> bool:
    """后台拉起一次 ingest --once；失败只记日志（文档仍留在 pending，可重建）"""
    root = _root()
    env = dict(os.environ)
    env["PYTHONPATH"] = root
    env.setdefault("PYTHONUNBUFFERED", "1")

    # 首选 systemd-run：一次性 unit，日志进 journal，服务重启也不受影响；
    # 同一时刻只允许一个（固定 unit 名，已存在则 systemd 直接拒绝 → 忽略即可，
    # 正在跑的那个 worker 会把任务表里的新任务一起捡走）
    if shutil.which("systemd-run"):
        cmd = [
            "systemd-run", "--collect", "--quiet", f"--unit={_UNIT}",
            f"--working-directory={root}",
            f"--property=Environment=PYTHONPATH={root}",
            "--property=Environment=PYTHONUNBUFFERED=1",
        ]
        env_file = os.path.join(root, ".env")
        if os.path.isfile(env_file):
            cmd.append(f"--property=EnvironmentFile={env_file}")
        cmd += [sys.executable, "-m", "src.infra.kb.ingest", "--once"]
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=20, env=env)
            if p.returncode == 0:
                log.info(f"知识库索引已触发（systemd-run）: {reason}")
                return True
            if b"already exists" in (p.stderr or b"") or b"already loaded" in (p.stderr or b""):
                log.info(f"知识库索引已在跑，跳过本次触发: {reason}")
                return True
            log.warning(f"systemd-run 触发失败，回退 Popen: "
                        f"{(p.stderr or b'').decode('utf-8', 'ignore')[:200]}")
        except Exception as e:
            log.warning(f"systemd-run 不可用，回退 Popen: {e}")

    # 兜底：脱离父进程直接跑（start_new_session，服务重启也不被带走）
    try:
        log_path = os.path.join(root, "data", "kb")
        os.makedirs(log_path, exist_ok=True)
        logf = open(os.path.join(log_path, "ingest.log"), "ab")
        subprocess.Popen(
            [sys.executable, "-m", "src.infra.kb.ingest", "--once"],
            cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=logf, stderr=logf,
            start_new_session=True, close_fds=True,
        )
        log.info(f"知识库索引已触发（Popen）: {reason}")
        return True
    except Exception as e:
        log.error(f"知识库索引触发失败（文档仍在排队，可点重建）: {e}", exc_info=True)
        return False
