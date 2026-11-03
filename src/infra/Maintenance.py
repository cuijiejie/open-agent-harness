"""每日维护：日志切分、旧日志清理、交付物过期清理

跑法（systemd timer 每天 03:00 调一次）：

    python -m src.infra.Maintenance            # 正常执行
    python -m src.infra.Maintenance --dry-run  # 只看会删什么，不动手

三件事
------
1. **日志切分**：把 `logs/app.log` 复制成 `logs/app_YYYYMMDD.log` 然后清空原文件
   （copytruncate 语义：gunicorn 的 6 个 worker 还拿着原 fd，直接 rename 会让它们
   继续往旧文件里写，所以只能"复制 + 截断"）
2. **日志清理**：删掉 365 天前的 `*_YYYYMMDD.log` 和 `.jsonl`（按天切的历史文件）
3. **交付物清理**：删掉 `artifacts/<用户>/<会话>/` 里 90 天没动过的目录
   （只删宿主副本；沙箱里的原件从来不动。产品口径：保留 90 天、不设每人上限）
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

from src.infra.utils.LogUtil import log

# 仓库根目录（src/infra/Maintenance.py → parents[2]）
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
LOG_DIR: Path = REPO_ROOT / "logs"
ARTIFACT_DIR: Path = REPO_ROOT / "artifacts"

# 产品口径
ARTIFACT_RETENTION_DAYS: int = int(os.environ.get("PA_ARTIFACT_RETENTION_DAYS", "90"))
LOG_RETENTION_DAYS: int = int(os.environ.get("PA_LOG_RETENTION_DAYS", "365"))

# 形如 app_20261029.log / app_20261029_1.log
_DATED_LOG_RE = re.compile(r"^(?P<stem>.+)_(?P<date>\d{8})(?:_\d+)?\.log$")


# --------------------------------------------------------------------------- #
# 1. 日志切分
# --------------------------------------------------------------------------- #
def rotate_logs(pairs: list[tuple[Path, Path]] | None = None, today: datetime | None = None,
                dry_run: bool = False) -> list[dict]:
    """把未切分的 *.log 复制成 `<名字>_YYYYMMDD.log` 并清空原文件（copytruncate）

    pairs: [(源目录, 归档目录)]。默认把仓库根目录的 gunicorn 日志也切进 logs/
    （根目录一直挂着 4~5MB 的 open_agent_harness.log，以前没人管）。

    今天已经切过的跳过（避免一天切多次）；空文件也跳过（省得造一堆 0 字节）。
    dry_run=True 时只报告"会切哪些"，**不动文件**。
    """
    today = today or datetime.now()
    stamp: str = today.strftime("%Y%m%d")
    pairs = pairs if pairs is not None else [(LOG_DIR, LOG_DIR), (REPO_ROOT, LOG_DIR)]
    result: list[dict] = []

    for src_dir, dst_dir in pairs:
        if not src_dir.is_dir():
            continue
        for path in sorted(src_dir.glob("*.log")):
            if _DATED_LOG_RE.match(path.name):        # 已经是切好的历史文件
                continue
            try:
                size: int = path.stat().st_size
            except OSError:
                continue
            if size <= 0:
                continue

            target: Path = dst_dir / f"{path.stem}_{stamp}.log"
            if target == path:                        # 理论上不会发生，防御一下
                continue
            # 同一天切多次（手动跑一次 + 定时再跑一次）→ 追加到同一个日期文件，
            # 不再造 app_YYYYMMDD_1.log / _2 这种碎文件
            if not dry_run:
                try:
                    dst_dir.mkdir(parents=True, exist_ok=True)
                    with open(path, "rb") as src, open(target, "ab") as dst:
                        shutil.copyfileobj(src, dst)
                    # 只截断、不删除：写日志的进程还持有原来的 fd，截断后它们继续往这里写
                    with open(path, "w", encoding="utf-8"):
                        pass
                except OSError as e:
                    log.warning(f"日志切分失败: {path.name}, err={e}")
                    continue

            result.append({"file": str(path), "rotated_to": str(target), "bytes": size})
            if not dry_run:
                log.info(f"日志已切分: {path.name} -> {target.name} ({size / 1024:.0f} KB)")
    return result


# --------------------------------------------------------------------------- #
# 2. 旧日志清理
# --------------------------------------------------------------------------- #
def prune_logs(log_dir: Path = LOG_DIR, days: int = LOG_RETENTION_DAYS,
               today: datetime | None = None, dry_run: bool = False) -> list[dict]:
    """删掉超过 days 天的历史日志（按文件名里的日期判断，不依赖 mtime）"""
    today = today or datetime.now()
    result: list[dict] = []
    if not log_dir.is_dir():
        return result

    for path in sorted(log_dir.iterdir()):
        if not path.is_file():
            continue
        stamp: str = ""
        m = _DATED_LOG_RE.match(path.name)
        if m:
            stamp = m.group("date")
        elif path.suffix in (".jsonl", ".log") and re.search(r"(\d{8})", path.name):
            # trace-20261029.jsonl / turn_metrics 之类
            found = re.search(r"(\d{8})", path.name)
            stamp = found.group(1) if found else ""
        if not stamp:
            continue
        try:
            file_day = datetime.strptime(stamp, "%Y%m%d")
        except ValueError:
            continue
        if (today - file_day).days <= days:
            continue
        try:
            size: int = path.stat().st_size
            if not dry_run:
                path.unlink()
            result.append({"file": path.name, "bytes": size, "days_old": (today - file_day).days})
            log.info(f"清理过期日志: {path.name}（{(today - file_day).days} 天前）")
        except OSError as e:
            log.warning(f"删除过期日志失败: {path.name}, err={e}")
    return result


# --------------------------------------------------------------------------- #
# 3. 交付物过期清理
# --------------------------------------------------------------------------- #
def cleanup_artifacts(root: Path = ARTIFACT_DIR, days: int = ARTIFACT_RETENTION_DAYS,
                      today: datetime | None = None, dry_run: bool = False) -> dict:
    """删掉 `artifacts/<用户>/<会话>/` 里 days 天没动过的整个目录

    - **只删宿主副本**，沙箱里的原件不动（用户还能从「我的文件」里拿）
    - 按"目录内最新文件时间"判断：整轮对话产的东西一起过期，避免删一半
    - `artifacts/<用户>/.owner` 这类文件不动，只动会话子目录
    """
    today = today or datetime.now()
    cutoff: datetime = today - timedelta(days=days)
    removed: list[dict] = []
    total_bytes: int = 0

    if not root.is_dir():
        return {"removed": [], "count": 0, "bytes": 0, "days": days}

    for user_dir in sorted(root.iterdir()):
        if not user_dir.is_dir():
            continue
        for sess in sorted(user_dir.iterdir()):
            if not sess.is_dir():
                continue
            newest: float = 0.0
            size: int = 0
            try:
                for p in sess.rglob("*"):
                    if p.is_file():
                        st = p.stat()
                        newest = max(newest, st.st_mtime)
                        size += st.st_size
                # 只有"目录里一个文件都没有"时才退回看目录本身的 mtime。
                # 不能把目录 mtime 算进"最近活动"：目录 mtime 只反映条目增删，
                # 新建一次会话目录就会把它刷新成当前时间 → 老文件永远删不掉。
                if not newest:
                    newest = sess.stat().st_mtime
            except OSError:
                continue
            if not newest:
                continue
            if datetime.fromtimestamp(newest) >= cutoff:
                continue
            if not dry_run:
                try:
                    shutil.rmtree(sess, ignore_errors=True)
                except OSError as e:
                    log.warning(f"删除过期交付物失败: {sess}, err={e}")
                    continue
            removed.append({"user": user_dir.name, "session": sess.name, "bytes": size})
            total_bytes += size
            log.info(f"清理过期交付物: {user_dir.name}/{sess.name} "
                     f"({size / 1024:.0f} KB, 最后改动 {datetime.fromtimestamp(newest):%Y-%m-%d})")

    return {"removed": removed, "count": len(removed), "bytes": total_bytes, "days": days}


# --------------------------------------------------------------------------- #
# 3. 沙箱维护：闲置停止 + 孤儿清理
# --------------------------------------------------------------------------- #
# 产品口径：
#   · 闲置 > 7 天 → **stop**（只停不删，workspace 保留，下次对话自动 start）
#   · 用户已不存在（离职/测试账号删了）且闲置/创建 > 3 天 → **delete**（真删，回收磁盘）
SANDBOX_IDLE_DAYS: int = int(os.environ.get("PA_SANDBOX_IDLE_DAYS", "7"))
SANDBOX_ORPHAN_GRACE_DAYS: int = int(os.environ.get("PA_SANDBOX_ORPHAN_GRACE_DAYS", "3"))


def _registered_sandbox_names() -> set[str] | None:
    """库里注册用户对应的合法沙箱名；查库失败返回 None（宁可不清理）"""
    try:
        import pymysql
        from config.Configs import Configs
        from src.agent.backends.sandbox import sandbox_manager

        c = Configs()
        conn = pymysql.connect(
            host=c.mysqlHost, port=int(c.mysqlPort), user=c.mysqlUser,
            password=c.mysqlPass, database=c.mysqlDB, charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT username FROM pa_users WHERE status<>'deleted'")
                users = [str(r["username"]) for r in cur.fetchall()]
        finally:
            conn.close()
        return {sandbox_manager.sandbox_name(u) for u in users}
    except Exception as e:
        log.warning(f"查询注册用户失败，跳过孤儿清理: {e}")
        return None


def stop_idle_sandboxes(days: int = SANDBOX_IDLE_DAYS, dry_run: bool = False) -> dict:
    """闲置超过 N 天的沙箱 → stop（保留 workspace，不删文件）"""
    from src.agent.backends.sandbox import sandbox_manager as sb

    if not getattr(sb, "enabled", False):
        return {"checked": 0, "stopped": [], "skipped": 0, "reason": "沙箱未启用"}

    items = sb.list_items()
    now = datetime.now().timestamp()
    cutoff = now - days * 86400
    stopped: list[str] = []
    skipped = 0
    unknown = 0
    for name, created in items:
        ts = sb.last_used(name)
        if ts is None:
            # 本功能上线前就存在的沙箱没有使用记录 → **从现在开始计时**（不立刻停）
            # 理由：宁可晚 7 天再停，也别把正在用的沙箱停掉（虽然只停不删，
            # 但会平白多一次冷启动，用户会觉得"今天特别慢"）。
            if not dry_run:
                sb.set_last_used(name, now)
            unknown += 1
            continue
        if ts > cutoff:
            skipped += 1
            continue
        if dry_run:
            stopped.append(name)
        elif sb.stop_sandbox(name):
            stopped.append(name)

    log.info(f"闲置沙箱停止: 检查 {len(items)} 个, 停止 {len(stopped)}, 保留 {skipped}, 未知 {unknown}, 口径 {days} 天")
    return {"checked": len(items), "stopped": stopped, "skipped": skipped,
            "unknown": unknown, "idle_days": days}


def cleanup_orphan_sandboxes(days: int = SANDBOX_ORPHAN_GRACE_DAYS, dry_run: bool = False) -> dict:
    """用户已不存在（库里没有）且超过宽限期的沙箱 → delete

    ⚠️ 这是**唯一**会删沙箱的地方（用户文件一起没），所以：
      · 只删「沙箱名对不上任何注册用户」的；
      · 查库失败直接放弃（宁可不删）；
      · 还有宽限期，刚建出来还没来得及入库的不会被误删。
    """
    from src.agent.backends.sandbox import sandbox_manager as sb

    if not getattr(sb, "enabled", False):
        return {"checked": 0, "deleted": [], "reason": "沙箱未启用"}

    valid = _registered_sandbox_names()
    if valid is None:
        return {"checked": 0, "deleted": [], "reason": "查询注册用户失败，跳过"}

    items = sb.list_items()
    now = datetime.now().timestamp()
    cutoff = now - days * 86400
    deleted: list[str] = []
    for name, created in items:
        if name in valid:
            continue
        ts = sb.last_used(name) or created or now
        if ts > cutoff:
            continue
        if dry_run:
            deleted.append(name)
        elif sb.delete_sandbox(name):
            deleted.append(name)

    log.info(f"孤儿沙箱清理: 检查 {len(items)} 个, 删除 {len(deleted)}, 口径 {days} 天")
    return {"checked": len(items), "deleted": deleted, "orphan_grace_days": days}


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 入口：**一个任务一件事**，各自独立 systemd timer 调用
#   logs           日志切分 + 过期清理
#   artifacts      交付物 90 天过期清理
#   sandbox-idle   闲置沙箱 stop（只停不删，7 天）
#   sandbox-orphan 孤儿沙箱 delete（用户已不存在，3 天）
#   all            一次全干（手动排查用；线上不再这么调）
# --------------------------------------------------------------------------- #
JOBS: tuple[str, ...] = ("logs", "artifacts", "sandbox-idle", "sandbox-orphan")


def run_job(job: str, dry_run: bool = False, idle_days: int = SANDBOX_IDLE_DAYS,
            orphan_days: int = SANDBOX_ORPHAN_GRACE_DAYS) -> dict:
    """执行单个维护任务，返回该任务的摘要"""
    started: datetime = datetime.now()
    log.info(f"=== 维护任务开始: {job}{'（dry-run）' if dry_run else ''} ===")

    summary: dict
    if job == "logs":
        rotated = rotate_logs(dry_run=dry_run)
        pruned = prune_logs(dry_run=dry_run)
        summary = {
            "rotated": len(rotated),
            "logs_deleted": len(pruned),
            "logs_freed_bytes": sum(x["bytes"] for x in pruned),
            "log_retention_days": LOG_RETENTION_DAYS,
        }
    elif job == "artifacts":
        cleaned = cleanup_artifacts(dry_run=dry_run)
        summary = {
            "artifacts_deleted": cleaned["count"],
            "artifacts_freed_bytes": cleaned["bytes"],
            "artifact_retention_days": ARTIFACT_RETENTION_DAYS,
        }
    elif job == "sandbox-idle":
        summary = stop_idle_sandboxes(days=idle_days, dry_run=dry_run)
    elif job == "sandbox-orphan":
        summary = cleanup_orphan_sandboxes(days=orphan_days, dry_run=dry_run)
    elif job == "all":
        merged: dict = {}
        for j in JOBS:
            merged[j] = run_job(j, dry_run=dry_run, idle_days=idle_days, orphan_days=orphan_days)
        merged["job"] = "all"
        merged["elapsed_s"] = round((datetime.now() - started).total_seconds(), 1)
        merged["dry_run"] = dry_run
        log.info(f"=== 全部维护任务结束: {merged} ===")
        return merged
    else:
        raise SystemExit(f"未知维护任务: {job}（可选: {', '.join(JOBS)}, all）")

    summary["job"] = job
    summary["elapsed_s"] = round((datetime.now() - started).total_seconds(), 1)
    summary["dry_run"] = dry_run
    log.info(f"=== 维护任务结束: {summary} ===")
    return summary


def run(dry_run: bool = False, idle_days: int = SANDBOX_IDLE_DAYS,
        orphan_days: int = SANDBOX_ORPHAN_GRACE_DAYS) -> dict:
    """兼容旧调用：一次跑全部（systemd 已改为按任务拆分，这里留给手动排查）"""
    return run_job("all", dry_run=dry_run, idle_days=idle_days, orphan_days=orphan_days)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="维护任务（一个任务一件事）：logs / artifacts / sandbox-idle / sandbox-orphan / all"
    )
    ap.add_argument("job", nargs="?", default="all", choices=[*JOBS, "all"],
                    help="要执行的任务（默认 all）")
    ap.add_argument("--dry-run", action="store_true", help="只打印会做什么，不真删/不真停")
    ap.add_argument("--idle-days", type=int, default=SANDBOX_IDLE_DAYS,
                    help=f"闲置多少天停沙箱（默认 {SANDBOX_IDLE_DAYS}，只停不删）")
    ap.add_argument("--orphan-days", type=int, default=SANDBOX_ORPHAN_GRACE_DAYS,
                    help=f"孤儿沙箱宽限多少天后删除（默认 {SANDBOX_ORPHAN_GRACE_DAYS}）")
    args = ap.parse_args(argv)
    summary = run_job(args.job, dry_run=args.dry_run,
                      idle_days=args.idle_days, orphan_days=args.orphan_days)
    print(summary)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
