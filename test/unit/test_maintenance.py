"""每日维护的单测（日志切分命名、过期日志清理、交付物 90 天判断）

这些逻辑一旦写错就是"删错文件"，所以要用临时目录把边界都钉住。
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _touch(path: Path, content: str = "x", age_days: float = 0,
           now: datetime | None = None) -> None:
    """把文件时间设成 `now - age_days`

    now 必须能显式指定：以前固定用 time.time()，用例里却传 today=datetime(2026,10,29)，
    真实日期一漂移（比如今天 10-30），"91 天前"就落到了 cutoff 另一侧，用例莫名失败。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if age_days:
        base = now.timestamp() if now is not None else time.time()
        ts = base - age_days * 86400
        os.utime(path, (ts, ts))


class TestRotateLogs(unittest.TestCase):
    def test_rotate_to_dated_name(self):
        import tempfile

        from src.infra.Maintenance import rotate_logs

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _touch(d / "app.log", "hello\n")
            _touch(d / "other.log", "world\n")
            _touch(d / "empty.log", "")

            done = rotate_logs([(d, d)], today=datetime(2026, 10, 29))
            names = {Path(x["file"]).name for x in done}
            self.assertEqual(names, {"app.log", "other.log"}, "空文件不该切、两个非空都要切")
            self.assertTrue((d / "app_20261029.log").exists())
            self.assertTrue((d / "other_20261029.log").exists())
            # 原文件被截断、fd 还在（copytruncate）
            self.assertEqual((d / "app.log").read_text(encoding="utf-8"), "")
            self.assertEqual((d / "app_20261029.log").read_text(encoding="utf-8"), "hello\n")

    def test_already_rotated_is_skipped(self):
        import tempfile

        from src.infra.Maintenance import rotate_logs

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _touch(d / "app_20260101.log", "old\n")
            _touch(d / "app.log", "today\n")
            done = rotate_logs([(d, d)], today=datetime(2026, 10, 29))
            self.assertEqual([Path(x["file"]).name for x in done], ["app.log"], "已切分的历史文件不能当天的源")

    def test_same_day_appends_to_same_file(self):
        """同一天切多次（手动 + 定时）要追加到同一个日期文件，不造 _1/_2 碎片"""
        import tempfile

        from src.infra.Maintenance import rotate_logs

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _touch(d / "app.log", "first\n")
            rotate_logs([(d, d)], today=datetime(2026, 10, 29))
            _touch(d / "app.log", "second\n")
            rotate_logs([(d, d)], today=datetime(2026, 10, 29))

            self.assertEqual(
                sorted(p.name for p in d.glob("*.log")),
                ["app.log", "app_20261029.log"],
                "同一天只应有一个日期文件",
            )
            self.assertEqual(
                (d / "app_20261029.log").read_text(encoding="utf-8"),
                "first\nsecond\n",
                "第二次切分应追加到同一个文件",
            )


    def test_root_log_archived_into_logs_dir(self):
        """仓库根的 gunicorn 日志要切到 logs/ 里（根目录别堆 5MB 的 log）"""
        import tempfile

        from src.infra.Maintenance import rotate_logs

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            logs = root / "logs"
            logs.mkdir()
            _touch(root / "open_agent_harness.log", "gunicorn line\n")

            done = rotate_logs([(logs, logs), (root, logs)], today=datetime(2026, 10, 29))
            self.assertEqual(len(done), 1, done)
            self.assertTrue((logs / "open_agent_harness_20261029.log").exists(), "应归档到 logs/")
            self.assertFalse((root / "open_agent_harness_20261029.log").exists(), "不要留在根目录")
            self.assertEqual((root / "open_agent_harness.log").read_text(encoding="utf-8"), "")

    def test_dry_run_does_not_rotate(self):
        """dry-run 只报告，不能真切（之前漏了，导致 dry-run 也把日志切走）"""
        import tempfile

        from src.infra.Maintenance import rotate_logs

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _touch(d / "app.log", "hello\n")
            done = rotate_logs([(d, d)], today=datetime(2026, 10, 29), dry_run=True)
            self.assertEqual([Path(x["file"]).name for x in done], ["app.log"], "dry-run 应该报告会切哪些")
            self.assertFalse((d / "app_20261029.log").exists(), "dry-run 不能真的生成日期文件")
            self.assertEqual((d / "app.log").read_text(encoding="utf-8"), "hello\n", "原文件不能被截断")


class TestPruneLogs(unittest.TestCase):
    def test_keeps_recent_deletes_old(self):
        import tempfile

        from src.infra.Maintenance import prune_logs

        today = datetime(2026, 10, 29)
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _touch(d / "app_20251001.log", "ancient")      # 一年前 → 删
            _touch(d / "app_20261001.log", "recent")       # 28 天前 → 留
            _touch(d / "trace-20250101.jsonl", "x")        # 一年多前 → 删
            _touch(d / "app.log", "current")               # 当前文件 → 不动

            pruned = prune_logs(d, days=365, today=today)
            names = {x["file"] for x in pruned}
            self.assertEqual(names, {"app_20251001.log", "trace-20250101.jsonl"}, names)
            self.assertFalse((d / "app_20251001.log").exists())
            self.assertTrue((d / "app_20261001.log").exists())
            self.assertTrue((d / "app.log").exists())

    def test_dry_run_deletes_nothing(self):
        import tempfile

        from src.infra.Maintenance import prune_logs

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _touch(d / "app_20200101.log", "old")
            pruned = prune_logs(d, days=365, today=datetime(2026, 10, 29), dry_run=True)
            self.assertEqual(len(pruned), 1)
            self.assertTrue((d / "app_20200101.log").exists(), "dry-run 不能真删")


class TestCleanupArtifacts(unittest.TestCase):
    def _mk(self, root: Path, user: str, sess: str, age_days: float, size: str = "x",
            now: datetime | None = None):
        p = root / user / sess / "file.txt"
        _touch(p, size, age_days=age_days, now=now)

    def test_only_expired_sessions_removed(self):
        import tempfile

        from src.infra.Maintenance import cleanup_artifacts

        today = datetime(2026, 10, 29)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mk(root, "alice", "old_sess", 120, now=today)   # 120 天 → 删
            self._mk(root, "alice", "new_sess", 10, now=today)    # 10 天 → 留
            self._mk(root, "bob", "edge89", 89, now=today)        # 89 天 → 留（没到 90）
            self._mk(root, "bob", "edge91", 91, now=today)        # 91 天 → 删
            _touch(root / "alice" / ".owner", "alice", age_days=999, now=today)  # 用户标记文件 → 不动

            res = cleanup_artifacts(root, days=90, today=today)
            self.assertEqual(res["count"], 2, res)
            self.assertFalse((root / "alice" / "old_sess").exists())
            self.assertTrue((root / "alice" / "new_sess").exists())
            self.assertTrue((root / "bob" / "edge89").exists())
            self.assertFalse((root / "bob" / "edge91").exists())
            self.assertTrue((root / "alice" / ".owner").exists(), "不能删用户目录下的 .owner")

    def test_uses_newest_file_in_session(self):
        """一个会话里只要还有新文件，整目录就不能删（别删一半）"""
        import tempfile

        from src.infra.Maintenance import cleanup_artifacts

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mk(root, "u", "s", 200)                       # 老文件
            _touch(root / "u" / "s" / "fresh.txt", "y", 1)      # 但有一个新的
            res = cleanup_artifacts(root, days=90, today=datetime(2026, 10, 29))
            self.assertEqual(res["count"], 0, "目录里有新文件时不该删")
            self.assertTrue((root / "u" / "s" / "fresh.txt").exists())

    def test_dry_run_keeps_everything(self):
        import tempfile

        from src.infra.Maintenance import cleanup_artifacts

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._mk(root, "u", "s", 500)
            res = cleanup_artifacts(root, days=90, today=datetime(2026, 10, 29), dry_run=True)
            self.assertEqual(res["count"], 1)
            self.assertTrue((root / "u" / "s").exists(), "dry-run 不能真删")

    def test_missing_root_is_safe(self):
        from src.infra.Maintenance import cleanup_artifacts

        res = cleanup_artifacts(Path("/no/such/dir/xyz"), days=90)
        self.assertEqual(res["count"], 0)


if __name__ == "__main__":
    unittest.main()
