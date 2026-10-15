import json
import unittest
from datetime import date

from activity_log import collector, export, report

from .helpers import make_app, t


class ReportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app, self.clock = make_app("18:00")
        self.addCleanup(self.app.close)
        db = self.app.storage
        db.add_raw_event(t("9:00"), "window", "POWERPNT.EXE", "手順書A.pptx - PowerPoint")
        db.add_raw_event(t("9:40"), "window", "Code.exe", "memo.md - work - Visual Studio Code")
        db.add_raw_event(t("10:00"), "idle")
        db.add_raw_event(t("10:30"), "window", "mstsc.exe", "srv - リモート デスクトップ接続")
        db.add_raw_event(t("12:00"), "stop")
        db.set_state(collector.KEY_HEARTBEAT, t("12:00"))
        ts = self.app.tasks
        ts.add_range("案件A/手順書", t("9:00"), t("10:00"))
        ts.add_range("案件A", t("10:30"), t("11:00"))
        ts.add_range("案件B", t("11:30"), t("12:00"))
        self.summary = report.build_daily(db, ts, self.app.settings, date(2026, 10, 15), self.clock())

    def test_totals(self) -> None:
        s = self.summary
        self.assertEqual(s.active_ms, 150 * 60_000)  # 9:00-10:00 + 10:30-12:00
        self.assertEqual(s.idle_ms, 30 * 60_000)
        self.assertEqual(s.assigned_ms, 120 * 60_000)
        self.assertEqual(s.active_unassigned_ms, 30 * 60_000)  # 11:00-11:30
        self.assertEqual((s.first_activity, s.last_activity), (t("9:00"), t("12:00")))

    def test_tree(self) -> None:
        top = {n.task.name: n for n in self.summary.task_totals}
        self.assertEqual(top["案件A"].total_ms, 90 * 60_000)
        self.assertEqual(top["案件A"].own_ms, 30 * 60_000)
        self.assertEqual(top["案件A"].children[0].task.name, "手順書")
        self.assertEqual(self.summary.task_totals[0].task.name, "案件A")  # 合計の多い順

    def test_timeline_has_gap_row(self) -> None:
        rows = [(r.start, r.end, r.assignment.task_path if r.assignment else None)
                for r in self.summary.timeline]
        self.assertIn((t("11:00"), t("11:30"), None), rows)
        # 離席だけの空白 (10:00-10:30) は未割当として出さない
        self.assertNotIn((t("10:00"), t("10:30"), None), rows)

    def test_windows(self) -> None:
        labels = [(w.app, w.label, w.ms // 60_000) for w in self.summary.windows]
        self.assertEqual(labels[0], ("リモート", "srv", 90))
        self.assertIn(("PowerPoint", "手順書A.pptx", 40), labels)

    def test_markdown(self) -> None:
        md = report.render_markdown(self.summary, self.app.settings)
        self.assertIn("# 日報 2026-10-15 (木)", md)
        self.assertIn("- 案件A: 1時間30分 (うち直接 30分)", md)
        self.assertIn("  - 手順書: 1時間00分", md)
        self.assertIn("*(未割当)*", md)
        self.assertIn("| リモート | srv | 1:30 |", md)

    def test_export_is_json_serializable(self) -> None:
        data = export.export(self.app.storage, self.app.tasks, tables=export.TABLES, now=self.clock())
        text = json.dumps(data, ensure_ascii=False)
        self.assertIn("手順書A.pptx", text)
        self.assertEqual(len(data["raw_events"]), 5)
        self.assertEqual(len(data["assignments"]), 3)
        json.dumps(self.summary.to_dict())


if __name__ == "__main__":
    unittest.main()
