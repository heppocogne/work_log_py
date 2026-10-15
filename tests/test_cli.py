import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from activity_log import cli


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "a.db")
        self.cfg = str(Path(self.tmp.name) / "settings.toml")

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--config", self.cfg, "--db", self.db, *args])
        return code, out.getvalue(), err.getvalue()

    def test_workflow(self) -> None:
        code, out, _ = self.run_cli("start", "案件A/作業", "--at", "-30m")
        self.assertEqual(code, 0, out)
        self.assertIn("開始: 案件A / 作業", out)
        code, out, _ = self.run_cli("status", "--json")
        data = json.loads(out)
        self.assertEqual(data["current"]["task_path"], "案件A / 作業")
        self.assertFalse(data["collector"]["running"])
        self.assertEqual(self.run_cli("assign", "add", "@1", "--from", "-20m", "--to", "-10m")[0], 0)
        code, out, _ = self.run_cli("log", "--json")
        self.assertEqual(len(json.loads(out)), 1)  # 同じタスクなので1つにまとまる
        self.assertEqual(self.run_cli("stop")[0], 0)
        code, out, _ = self.run_cli("report")
        self.assertIn("# 日報", out)
        code, out, _ = self.run_cli("export")
        self.assertEqual(json.loads(out)["format"], "activity-log-export")
        code, out, _ = self.run_cli()  # コマンド省略 = status
        self.assertIn("現在のタスク: (なし)", out)

    def test_errors_return_1(self) -> None:
        code, _, err = self.run_cli("stop")
        self.assertEqual(code, 1)
        self.assertIn("進行中のタスクはありません", err)
        code, _, err = self.run_cli("start", "A", "--at", "xx")
        self.assertEqual(code, 1)

    def test_config_init_and_show(self) -> None:
        self.assertEqual(self.run_cli("config", "init")[0], 0)
        self.assertTrue(os.path.exists(self.cfg))
        code, out, _ = self.run_cli("config", "show", "--json")
        self.assertEqual(json.loads(out)["poll_interval_ms"], 1000)
        self.assertEqual(self.run_cli("config", "init")[0], 1)

    def test_watch_status_and_stop_without_collector(self) -> None:
        code, out, _ = self.run_cli("watch", "stop")
        self.assertIn("実行されていません", out)
        code, out, _ = self.run_cli("watch", "status")
        self.assertIn("ウィンドウ監視: 停止中", out)

    def test_join_negative_times(self) -> None:
        self.assertEqual(cli._join_negative_times(["start", "A", "--at", "-1h30m"]),
                         ["start", "A", "--at=-1h30m"])
        self.assertEqual(cli._join_negative_times(["--at", "-x"]), ["--at", "-x"])


if __name__ == "__main__":
    unittest.main()
