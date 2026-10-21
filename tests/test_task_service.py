import unittest

from activity_log.errors import ActivityLogError, NotFoundError

from .helpers import make_app, t


class AssignmentTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.app, self.clock = make_app()
        self.ts = self.app.tasks
        self.addCleanup(self.app.close)

    def rows(self) -> list[tuple[str, int, int | None]]:
        return [(a.task_path, a.start_at, a.end_at) for a in self.ts.list_assignments(None, None)]

    def assertNoOverlap(self) -> None:
        items = self.ts.list_assignments(None, None)
        opened = [a for a in items if a.end_at is None]
        self.assertLessEqual(len(opened), 1)
        for prev, nxt in zip(items, items[1:]):
            self.assertIsNotNone(prev.end_at)
            self.assertLessEqual(prev.end_at, nxt.start_at)


class TaskTreeTest(AssignmentTestBase):
    def test_initial_tasks(self) -> None:
        self.assertEqual([t.path for t in self.ts.list_tasks()], ["未分類"])

    def test_resolve_creates_hierarchy(self) -> None:
        r = self.ts.resolve(" 案件A / 手順書 ", create=True)
        self.assertEqual(r.task.path, "案件A / 手順書")
        self.assertEqual(r.created, ["案件A", "案件A / 手順書"])
        again = self.ts.resolve("案件A/手順書")
        self.assertEqual(again.task.id, r.task.id)
        self.assertEqual(again.created, [])

    def test_resolve_by_id_and_recent(self) -> None:
        a = self.ts.resolve("A", create=True).task
        b = self.ts.resolve("B", create=True).task
        self.assertEqual(self.ts.resolve(f"#{a.id}").task.id, a.id)
        self.ts.switch("A", at=t("9:00"))
        self.ts.switch("B", at=t("10:00"))
        self.assertEqual(self.ts.resolve("@1").task.id, b.id)
        self.assertEqual(self.ts.resolve("@2").task.id, a.id)
        with self.assertRaises(NotFoundError):
            self.ts.resolve("@3")

    def test_resolve_missing(self) -> None:
        with self.assertRaises(NotFoundError):
            self.ts.resolve("なし")

    def test_invalid_names(self) -> None:
        for bad in ("#x", "@foo"):
            with self.assertRaises(ActivityLogError):
                self.ts.resolve(bad, create=True)

    def test_rename_move_archive(self) -> None:
        self.ts.resolve("A/x", create=True)
        self.ts.resolve("B", create=True)
        self.assertEqual(self.ts.rename_task("A/x", "y").path, "A / y")
        self.assertEqual(self.ts.move_task("A/y", "B").path, "B / y")
        self.assertEqual(self.ts.move_task("B/y", None).path, "y")
        with self.assertRaises(ActivityLogError):
            self.ts.move_task("B", "B")
        self.ts.set_archived("B", True)
        self.assertNotIn("B", [t.path for t in self.ts.list_tasks()])
        with self.assertRaises(ActivityLogError):
            self.ts.switch("B", at=t("9:00"))

    def test_move_into_descendant_rejected(self) -> None:
        self.ts.resolve("A/B/C", create=True)
        with self.assertRaises(ActivityLogError):
            self.ts.move_task("A", "A/B/C")

    def test_merge(self) -> None:
        self.ts.add_range("A", t("9:00"), t("10:00"))
        self.ts.add_range("B", t("10:00"), t("11:00"))
        src, dst, n = self.ts.merge_task("A", "B")
        self.assertEqual(n, 1)
        self.assertTrue(src.archived)
        self.assertEqual({p for p, _, _ in self.rows()}, {"B"})


class SwitchStopTest(AssignmentTestBase):
    def test_switch_closes_previous(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.ts.switch("B", at=t("10:00"))
        self.assertEqual(self.rows(), [("A", t("9:00"), t("10:00")), ("B", t("10:00"), None)])
        self.assertEqual(self.ts.current().task_path, "B")

    def test_switch_same_task_is_noop(self) -> None:
        a1, _ = self.ts.switch("A", at=t("9:00"))
        a2, _ = self.ts.switch("A", at=t("9:30"))
        self.assertEqual(a1.id, a2.id)
        self.assertEqual(self.rows(), [("A", t("9:00"), None)])

    def test_switch_same_task_earlier_extends(self) -> None:
        self.ts.switch("A", at=t("9:30"))
        self.ts.switch("A", at=t("9:00"))
        self.assertEqual(self.rows(), [("A", t("9:00"), None)])

    def test_switch_backdated_truncates_later(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.ts.switch("B", at=t("10:00"))
        self.ts.switch("C", at=t("11:00"))
        self.ts.switch("D", at=t("9:30"))  # 9:30 以降はすべて D
        self.assertEqual(self.rows(), [("A", t("9:00"), t("9:30")), ("D", t("9:30"), None)])
        self.assertNoOverlap()

    def test_switch_future_rejected(self) -> None:
        with self.assertRaises(ActivityLogError):
            self.ts.switch("A", at=t("19:00"))

    def test_stop(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        a = self.ts.stop(at=t("9:45"))
        self.assertEqual(a.end_at, t("9:45"))
        self.assertIsNone(self.ts.current())
        with self.assertRaises(ActivityLogError):
            self.ts.stop()

    def test_stop_before_start_rejected(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        with self.assertRaises(ActivityLogError):
            self.ts.stop(at=t("8:00"))

    def test_resume_merges_adjacent(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.ts.stop(at=t("10:00"))
        self.ts.resume(at=t("10:00"))
        self.assertEqual(self.rows(), [("A", t("9:00"), None)])

    def test_resume_after_gap(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.ts.stop(at=t("10:00"))
        self.ts.resume(at=t("11:00"))
        self.assertEqual(self.rows(), [("A", t("9:00"), t("10:00")), ("A", t("11:00"), None)])
        with self.assertRaises(ActivityLogError):
            self.ts.resume()


class CarveTest(AssignmentTestBase):
    """重なりの切り詰め (境界条件)。"""

    def setUp(self) -> None:
        super().setUp()
        self.ts.add_range("A", t("9:00"), t("10:00"))
        self.ts.add_range("B", t("10:00"), t("11:00"))
        self.ts.add_range("C", t("11:00"), t("12:00"))

    def test_inside_splits(self) -> None:
        self.ts.add_range("X", t("10:15"), t("10:45"))
        self.assertEqual(self.rows(), [
            ("A", t("9:00"), t("10:00")),
            ("B", t("10:00"), t("10:15")),
            ("X", t("10:15"), t("10:45")),
            ("B", t("10:45"), t("11:00")),
            ("C", t("11:00"), t("12:00")),
        ])

    def test_overlap_tail_and_head(self) -> None:
        self.ts.add_range("X", t("9:30"), t("11:30"))
        self.assertEqual(self.rows(), [
            ("A", t("9:00"), t("9:30")),
            ("X", t("9:30"), t("11:30")),
            ("C", t("11:30"), t("12:00")),
        ])

    def test_exact_cover_replaces(self) -> None:
        self.ts.add_range("X", t("10:00"), t("11:00"))
        self.assertEqual([p for p, _, _ in self.rows()], ["A", "X", "C"])

    def test_touching_boundaries_untouched(self) -> None:
        self.ts.add_range("X", t("12:00"), t("13:00"))
        self.ts.add_range("Y", t("8:00"), t("9:00"))
        self.assertEqual([p for p, _, _ in self.rows()], ["Y", "A", "B", "C", "X"])

    def test_cover_all(self) -> None:
        self.ts.add_range("X", t("8:00"), t("13:00"))
        self.assertEqual(self.rows(), [("X", t("8:00"), t("13:00"))])

    def test_same_task_adjacent_merges(self) -> None:
        self.ts.add_range("A", t("10:00"), t("10:30"))
        self.assertEqual(self.rows()[0], ("A", t("9:00"), t("10:30")))
        self.assertNoOverlap()

    def test_split_open_assignment(self) -> None:
        self.ts.switch("D", at=t("13:00"))
        self.ts.add_range("X", t("14:00"), t("14:30"))
        self.assertEqual(self.rows()[-3:], [
            ("D", t("13:00"), t("14:00")),
            ("X", t("14:00"), t("14:30")),
            ("D", t("14:30"), None),
        ])
        self.assertNoOverlap()

    def test_invalid_range(self) -> None:
        with self.assertRaises(ActivityLogError):
            self.ts.add_range("X", t("10:00"), t("10:00"))
        with self.assertRaises(ActivityLogError):
            self.ts.add_range("X", t("17:00"), t("19:00"))


class EditTest(AssignmentTestBase):
    def test_edit_extends_and_trims_neighbor(self) -> None:
        a, _ = self.ts.add_range("A", t("9:00"), t("10:00"))
        self.ts.add_range("B", t("10:00"), t("11:00"))
        self.ts.edit(a.id, end=t("10:30"))
        self.assertEqual(self.rows(), [("A", t("9:00"), t("10:30")), ("B", t("10:30"), t("11:00"))])
        self.assertEqual(self.ts.get_assignment(a.id).source, "manual")

    def test_edit_task(self) -> None:
        a, _ = self.ts.add_range("A", t("9:00"), t("10:00"))
        _, created = self.ts.edit(a.id, task_spec="Z/z")
        self.assertEqual(created, ["Z", "Z / z"])
        self.assertEqual(self.rows(), [("Z / z", t("9:00"), t("10:00"))])

    def test_edit_open_start(self) -> None:
        self.ts.add_range("A", t("9:00"), t("10:00"))
        cur, _ = self.ts.switch("B", at=t("10:00"))
        self.ts.edit(cur.id, start=t("9:30"))
        self.assertEqual(self.rows(), [("A", t("9:00"), t("9:30")), ("B", t("9:30"), None)])

    def test_edit_invalid(self) -> None:
        a, _ = self.ts.add_range("A", t("9:00"), t("10:00"))
        with self.assertRaises(ActivityLogError):
            self.ts.edit(a.id, start=t("10:30"))

    def test_delete(self) -> None:
        a, _ = self.ts.add_range("A", t("9:00"), t("10:00"))
        self.ts.delete(a.id)
        self.assertEqual(self.rows(), [])
        with self.assertRaises(NotFoundError):
            self.ts.delete(a.id)

    def test_resolve_assignment(self) -> None:
        a, _ = self.ts.add_range("A", t("9:00"), t("10:00"))
        b, _ = self.ts.add_range("B", t("10:00"), t("11:00"))
        self.assertEqual(self.ts.resolve_assignment(f"#{a.id}"), a.id)
        self.assertEqual(self.ts.resolve_assignment("@1"), b.id)
        self.assertEqual(self.ts.resolve_assignment("@2"), a.id)
        for bad in ("@0", "@3"):
            with self.assertRaises(NotFoundError):
                self.ts.resolve_assignment(bad)
        self.assertEqual(self.ts.resolve_assignment(str(a.id)), a.id)
        for bad in ("#", "@x", ""):
            with self.assertRaises(ActivityLogError):
                self.ts.resolve_assignment(bad)

    def test_close_open_before(self) -> None:
        self.ts.switch("A", at=t("9:00"))
        self.assertIsNone(self.ts.close_open_before(t("8:00")))
        closed = self.ts.close_open_before(t("9:30"))
        self.assertEqual(closed.end_at, t("9:30"))

    def test_event_range(self) -> None:
        db = self.app.storage
        e1 = db.add_raw_event(t("9:00"), "window", "a.exe", "a")
        e2 = db.add_raw_event(t("9:10"), "window", "b.exe", "b")
        db.add_raw_event(t("9:25"), "idle")
        self.assertEqual(self.ts.event_range(e1.id, e2.id), (t("9:00"), t("9:25")))
        self.assertEqual(self.ts.event_range(e2.id, e1.id), (t("9:00"), t("9:25")))


if __name__ == "__main__":
    unittest.main()
