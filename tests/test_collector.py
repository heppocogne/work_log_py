import unittest

from activity_log import collector
from activity_log.collector import Collector
from activity_log.winapi import WindowInfo

from .helpers import make_app, t


class FakeSource:
    def __init__(self) -> None:
        self.window: WindowInfo | None = None
        self.idle = 0

    def set(self, process: str | None, title: str, pid: int = 100) -> None:
        self.window = WindowInfo(1, title, process, pid)

    def foreground(self) -> WindowInfo | None:
        return self.window

    def idle_ms(self) -> int:
        return self.idle


class CollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app, self.clock = make_app("9:00", idle_threshold_minutes=5, exclude_processes=["LockApp.exe"])
        self.addCleanup(self.app.close)
        self.src = FakeSource()
        self.c = Collector(self.app.storage, self.app.tasks, self.app.settings, self.src,
                           clock=self.clock, pid=999)

    def events(self) -> list[tuple[str, int, str | None]]:
        return [(e.kind, e.started_at, e.title) for e in self.app.storage.raw_events_between(None, None)]

    def run_seconds(self, seconds: int) -> None:
        for _ in range(seconds):
            self.clock.advance(1)
            self.c.tick()

    def test_records_only_changes(self) -> None:
        self.c.start()
        self.src.set("POWERPNT.EXE", "手順書A.pptx - PowerPoint")
        self.c.tick()
        self.run_seconds(5)
        self.src.set("Code.exe", "memo.md - work - Visual Studio Code")
        self.run_seconds(1)
        kinds = [(k, title) for k, _, title in self.events()]
        self.assertEqual(kinds, [("window", "手順書A.pptx - PowerPoint"),
                                 ("window", "memo.md - work - Visual Studio Code")])

    def test_skips_empty_self_and_excluded(self) -> None:
        self.c.start()
        self.src.window = None
        self.c.tick()
        self.src.set("x.exe", "  ")
        self.run_seconds(1)
        self.src.set("python.exe", "overlay", pid=999)
        self.run_seconds(1)
        self.src.set("LockApp.exe", "lock")
        self.run_seconds(1)
        self.assertEqual(self.events(), [])

    def test_idle_is_backdated_and_resume_records_window(self) -> None:
        self.c.start()
        self.src.set("a.exe", "A")
        self.c.tick()
        start = self.clock.ms
        self.src.idle = 0
        # 6分間操作なし
        for _ in range(360):
            self.clock.advance(1)
            self.src.idle += 1000
            self.c.tick()
        kinds = self.events()
        self.assertEqual(kinds[-1][0], "idle")
        self.assertEqual(kinds[-1][1], start)  # 最後の操作時刻にさかのぼる
        # 離席中はウィンドウが変わっても記録しない
        self.src.set("b.exe", "B")
        self.run_seconds(3)
        self.assertEqual(self.events()[-1][0], "idle")
        # 操作再開 → 同じウィンドウでも window を記録
        self.src.idle = 0
        self.run_seconds(6)
        self.assertEqual(self.events()[-1][:1] + self.events()[-1][2:], ("window", "B"))
        self.assertFalse(collector.status(self.app.storage, self.app.settings, self.clock.ms).idle)

    def test_sleep_gap_records_stop(self) -> None:
        self.c.start()
        self.src.set("a.exe", "A")
        self.c.tick()
        before = self.clock.ms
        self.clock.advance(3600)  # スリープ
        self.c.tick()
        kinds = [(k, at) for k, at, _ in self.events()]
        self.assertEqual(kinds[-2], ("stop", before))
        self.assertEqual(kinds[-1], ("window", self.clock.ms))

    def test_shutdown_and_recovery(self) -> None:
        self.app.tasks.switch("作業", at=t("8:30"))
        self.c.start()
        self.src.set("a.exe", "A")
        self.c.tick()
        self.run_seconds(120)  # 生存時刻が更新される
        hb = self.app.storage.get_state_int(collector.KEY_HEARTBEAT)
        self.assertEqual(hb, t("9:02"))
        # 異常終了 (shutdown を呼ばない) → 翌朝起動
        self.clock.ms = t("9:00", day=1)
        c2 = Collector(self.app.storage, self.app.tasks, self.app.settings, self.src,
                       clock=self.clock, pid=1000)
        rec = c2.start()
        self.assertIsNotNone(rec)
        self.assertEqual(rec.stop_at, hb)
        self.assertEqual(rec.closed.end_at, hb)
        self.assertEqual(self.events()[-1][:2], ("stop", hb))
        c2.shutdown()
        self.assertIsNone(c2.recover())  # 正常終了後は補正しない

    def test_status_and_stop_request(self) -> None:
        st = collector.status(self.app.storage, self.app.settings, self.clock.ms)
        self.assertFalse(st.running)
        self.c.start()
        st = collector.status(self.app.storage, self.app.settings, self.clock.ms)
        self.assertTrue(st.running)
        self.assertEqual(st.pid, 999)
        self.assertFalse(self.c.stop_requested())
        self.clock.advance(1)
        collector.request_stop(self.app.storage, self.clock.ms)
        self.assertTrue(self.c.stop_requested())
        ticks = []
        collector.run_loop(self.c, 1000, sleep=lambda s: ticks.append(s))
        self.assertEqual(ticks, [])
        self.c.shutdown()
        self.clock.advance(600)
        self.assertFalse(collector.status(self.app.storage, self.app.settings, self.clock.ms).running)


if __name__ == "__main__":
    unittest.main()
