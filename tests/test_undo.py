import unittest

from activity_log.errors import ActivityLogError, NotFoundError

from .helpers import t
from .test_task_service import AssignmentTestBase


class UndoTest(AssignmentTestBase):
    def test_nothing_to_undo(self) -> None:
        with self.assertRaises(ActivityLogError):
            self.ts.undo()

    def test_undo_switch_removes_created_task_and_restores_open(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.ts.switch("B/c", at=t("10:00"))
        self.ts.undo()
        self.assertEqual(self.rows(), [("A", t("9:00"), None)])
        self.assertEqual([x.path for x in self.ts.list_tasks()], ["A", "未分類"])
        self.ts.undo()
        self.assertEqual(self.rows(), [])
        with self.assertRaises(ActivityLogError):
            self.ts.undo()

    def test_undo_restores_carved_assignments(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.ts.add_range("B", t("10:00"), t("10:30"))
        self.assertEqual(len(self.rows()), 3)
        self.ts.undo()
        self.assertEqual(self.rows(), [("A", t("9:00"), None)])

    def test_undo_resume_is_one_entry(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.ts.stop(at=t("10:00"))
        self.ts.resume(at=t("11:00"))
        self.assertEqual(len(self.ts.undo_history()), 3)
        self.ts.undo()
        self.assertEqual(self.rows(), [("A", t("9:00"), t("10:00"))])

    def test_task_operations(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.ts.switch("B", at=t("10:00"))
        self.ts.rename_task("B", "B2")
        self.ts.undo()
        self.assertIn("B", [x.path for x in self.ts.list_tasks()])
        self.ts.merge_task("B", "A")
        self.assertEqual({r[0] for r in self.rows()}, {"A"})
        self.ts.undo()
        self.assertEqual([r[0] for r in self.rows()], ["A", "B"])
        self.assertFalse(self.ts.get_task(self.ts.resolve("B").task.id).archived)
        self.ts.set_archived("A", True)
        self.ts.move_task("B", "A")
        self.ts.undo()
        self.ts.undo()
        self.assertFalse(self.ts.resolve("A").task.archived)

    def test_noop_and_failed_operations_not_recorded(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        n = len(self.ts.undo_history())
        self.ts.switch("A", at=t("9:30"))  # 既に実行中
        with self.assertRaises(ActivityLogError):
            self.ts.stop(at=t("8:00"))
        with self.assertRaises(ActivityLogError):
            self.ts.switch("X/y", at=t("23:59", day=1))  # 未来。タスクも作られない
        self.assertEqual(len(self.ts.undo_history()), n)
        self.assertEqual([x.path for x in self.ts.list_tasks()], ["A", "未分類"])

    def test_undo_to_id_and_limit(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.ts.switch("B", at=t("10:00"))
        self.ts.switch("C", at=t("11:00"))
        history = self.ts.undo_history()
        undone = self.ts.undo(history[1].id)
        self.assertEqual(len(undone), 2)
        self.assertEqual(self.rows(), [("A", t("9:00"), None)])
        with self.assertRaises(NotFoundError):
            self.ts.undo(9999)
        for i in range(25):
            self.ts.add_range("B", t("12:00") + i * 60_000, t("12:00") + i * 60_000 + 1000)
        self.assertEqual(len(self.ts.undo_history()), 20)

    def test_close_open_before_not_recorded(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        n = len(self.ts.undo_history())
        self.ts.close_open_before(t("9:30"))
        self.assertEqual(len(self.ts.undo_history()), n)


if __name__ == "__main__":
    unittest.main()
