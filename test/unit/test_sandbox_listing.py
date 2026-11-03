"""沙箱列目录：把两次 exec 合并成一次后的解析逻辑

为什么要测：`/api/sandbox/files` 原来 exec 两次（~4.2s），合并成一次（~1.3s）。
合并后是靠 TAB 前缀分流解析的，解析错了用户就会看到错乱的目录/文件列表。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestParseEntries(unittest.TestCase):
    def _parse(self, text: str, cur="/sandbox"):
        from src.controller.SandboxController import _parse_entries

        return _parse_entries(text, cur)

    def test_dirs_and_files(self):
        out = (
            "D\toutput\t3\t2026-10-29 03:40\n"
            "D\tupload\t1\t2026-10-28 10:00\n"
            "F\t1024\t2026-10-29 03:41\treport.xlsx\n"
            "F\t512\t2026-10-29 03:42\tchart.png\n"
        )
        dirs, files, total = self._parse(out)
        self.assertEqual([d["name"] for d in dirs], ["output", "upload"], "系统目录排前面")
        self.assertEqual(dirs[0]["count"], 3)
        self.assertEqual(dirs[0]["mtime"], "2026-10-29 03:40")
        self.assertEqual([f["name"] for f in files], ["chart.png", "report.xlsx"], "文件按时间倒序")
        self.assertEqual(total, 1536)
        self.assertEqual(files[0]["path"], "/sandbox/chart.png")
        self.assertEqual(files[0]["ext"], ".png")
        self.assertTrue(files[0]["inline"], "png 可内联打开")
        self.assertFalse(files[1]["inline"], "xlsx 不能内联")

    def test_skips_hidden_and_skills(self):
        out = (
            "D\tskills\t5\t2026-10-29 03:00\n"
            "D\tskills-disabled\t1\t2026-10-29 03:00\n"
            "D\t.tmp\t0\t2026-10-29 03:00\n"
            "D\tmydir\t2\t2026-10-29 03:00\n"
        )
        dirs, files, total = self._parse(out)
        self.assertEqual([d["name"] for d in dirs], ["mydir"])
        self.assertEqual(files, [])
        self.assertEqual(total, 0)

    def test_tolerates_garbage(self):
        """命令部分失败/输出被截断时不能崩，也不能把垃圾当条目"""
        out = (
            "D\tonlyTwoFields\t3\n"           # 缺时间也算目录（时间留空）
            "F\tabc\t2026-10-29\tfile.txt\n"  # 大小非数字 → 0
            "noise line without tabs\n"
            "F\t10\t2026-10-29\t\n"           # 空文件名 → 丢
            ""
        )
        dirs, files, total = self._parse(out)
        self.assertEqual([d["name"] for d in dirs], ["onlyTwoFields"])
        self.assertEqual([f["name"] for f in files], ["file.txt"])
        self.assertEqual(files[0]["size"], 0)
        self.assertEqual(total, 0)

    def test_empty_output(self):
        self.assertEqual(self._parse(""), ([], [], 0))

    def test_subdir_path_prefix(self):
        out = "F\t7\t2026-10-29 03:00\ta.txt\n"
        _, files, _ = self._parse(out, cur="/sandbox/output")
        self.assertEqual(files[0]["path"], "/sandbox/output/a.txt")


class TestEntriesCmd(unittest.TestCase):
    def test_single_command_contains_both_parts(self):
        """必须是一条命令里同时列目录和文件（合并的意义就在这）"""
        from src.controller.SandboxController import _entries_cmd

        cmd = _entries_cmd("/sandbox")
        self.assertIn("for d in */", cmd, "要列子目录")
        self.assertIn("-printf 'F\\t", cmd, "要列文件")
        self.assertIn("cd '/sandbox'", cmd, "路径要安全引用")
        self.assertEqual(cmd.count("; find . -maxdepth 1"), 1)

    def test_path_quoting(self):
        from src.controller.SandboxController import _entries_cmd

        cmd = _entries_cmd("/sandbox/a'b")
        self.assertIn("'/sandbox/a'\\''b'", cmd, "含单引号的路径要转义")


class TestDeleteHelpers(unittest.TestCase):
    """删除/批量删除合并成一次 exec 后的命令与解析"""

    def test_delete_cmd_has_all_phases(self):
        from src.controller.SandboxController import _delete_cmd

        cmd = _delete_cmd("/sandbox/a.txt", force=False)
        for mark in ("KIND=none", "KIND=file", "KIND=dir", "RESULT=need_confirm",
                     "RESULT=still_there", "RESULT=deleted"):
            self.assertIn(mark, cmd, f"命令里缺少 {mark}")
        self.assertIn("rm -f", cmd)

    def test_delete_cmd_force_skips_confirm(self):
        from src.controller.SandboxController import _delete_cmd

        self.assertNotIn("need_confirm", _delete_cmd("/sandbox/d", force=True))

    def test_delete_cmd_quotes_path(self):
        from src.controller.SandboxController import _delete_cmd

        self.assertIn("'/sandbox/a'\\''b'", _delete_cmd("/sandbox/a'b", force=False))

    def test_parse_kv(self):
        from src.controller.SandboxController import _parse_kv

        out = "KIND=dir\nCOUNT=3\nRESULT=deleted\n\nnoise\n"
        self.assertEqual(_parse_kv(out), {"KIND": "dir", "COUNT": "3", "RESULT": "deleted"})
        self.assertEqual(_parse_kv(""), {})

    def test_batch_cmd_lists_every_path(self):
        from src.controller.SandboxController import _batch_delete_cmd

        cmd = _batch_delete_cmd(["/sandbox/a.txt", "/sandbox/b c.txt"])
        self.assertIn("'/sandbox/a.txt'", cmd)
        self.assertIn("'/sandbox/b c.txt'", cmd)
        self.assertIn("MISS", cmd)
        self.assertIn("FAIL", cmd)
        self.assertIn("OK", cmd)

    def test_parse_batch(self):
        from src.controller.SandboxController import _parse_batch

        out = "OK\t/sandbox/a.txt\nMISS\t/sandbox/b.txt\nFAIL\t/sandbox/c.txt\n"
        deleted, failed = _parse_batch(out)
        self.assertEqual(deleted, ["/sandbox/a.txt"])
        self.assertEqual([f["path"] for f in failed], ["/sandbox/b.txt", "/sandbox/c.txt"])
        self.assertEqual(failed[0]["reason"], "不存在")
        self.assertIn("未生效", failed[1]["reason"])

    def test_parse_batch_ignores_garbage(self):
        from src.controller.SandboxController import _parse_batch

        deleted, failed = _parse_batch("noise\n\nOK\n")
        self.assertEqual((deleted, failed), ([], []))


class TestOpHelpers(unittest.TestCase):
    """移动/复制合并成一次 exec 后的命令与解析"""

    def test_move_cmd_reports_conflict(self):
        from src.controller.SandboxController import _op_cmd

        cmd = _op_cmd("/sandbox/dest", ["/sandbox/a"], copy=False)
        self.assertIn("NODEST", cmd, "目标不存在时要有明确标记")
        self.assertIn("CONFLICT", cmd, "同名要报冲突（不能像以前那样假报成功）")
        self.assertIn("mv ", cmd)
        self.assertIn("'/sandbox/a'", cmd)

    def test_copy_cmd_auto_suffix(self):
        from src.controller.SandboxController import _op_cmd

        cmd = _op_cmd("/sandbox/dest", ["/sandbox/a.txt"], copy=True)
        self.assertIn("cp -r", cmd)
        self.assertIn('while [ -e "$d/$c" ]', cmd, "同名要自增后缀")
        self.assertIn("${pre}_$n${suf}", cmd)
        # 回归：候选名不能复用 t（t 是 "$d/$b"，再拼就是 "$d/$d/$b"，cp 必失败）
        self.assertIn('cp -r "$s" "$d/$c"', cmd)
        self.assertNotIn('cp -r "$s" "$d/$t"', cmd)

    def test_op_cmd_lists_every_source(self):
        from src.controller.SandboxController import _op_cmd

        cmd = _op_cmd("/sandbox/d", ["/sandbox/a", "/sandbox/b c"], copy=False)
        self.assertIn("'/sandbox/a'", cmd)
        self.assertIn("'/sandbox/b c'", cmd)

    def test_parse_op_result(self):
        from src.controller.SandboxController import _parse_op_result

        out = "OK\t/sandbox/a\nMISS\t/sandbox/b\nCONFLICT\t/sandbox/c\nFAIL\t/sandbox/d\n"
        ok, failed, nodest = _parse_op_result(out)
        self.assertEqual(ok, ["/sandbox/a"])
        self.assertEqual([f["path"] for f in failed], ["/sandbox/b", "/sandbox/c", "/sandbox/d"])
        self.assertFalse(nodest)
        self.assertIn("同名", failed[1]["reason"])

    def test_parse_op_result_nodest(self):
        from src.controller.SandboxController import _parse_op_result

        ok, failed, nodest = _parse_op_result("NODEST\n")
        self.assertEqual((ok, failed), ([], []))
        self.assertTrue(nodest)

    def test_parse_op_result_ignores_garbage(self):
        from src.controller.SandboxController import _parse_op_result

        self.assertEqual(_parse_op_result("noise\nOK\n"), ([], [], False))


if __name__ == "__main__":
    unittest.main()
