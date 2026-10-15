import unittest
from datetime import date

from activity_log.errors import ActivityLogError
from activity_log.timeutil import day_range, fmt_duration, logical_date, parse_date, parse_time

from .helpers import t


class ParseTest(unittest.TestCase):
    now = t("18:00")
    today = date(2026, 10, 15)

    def p(self, s: str, **kw: object) -> int:
        return parse_time(s, now=self.now, **kw)  # type: ignore[arg-type]

    def test_relative(self) -> None:
        self.assertEqual(self.p("now"), self.now)
        self.assertEqual(self.p("-15m"), t("17:45"))
        self.assertEqual(self.p("-1h30m"), t("16:30"))
        self.assertEqual(self.p("15分前"), t("17:45"))
        self.assertEqual(self.p("1時間前"), t("17:00"))

    def test_clock(self) -> None:
        self.assertEqual(self.p("9:30"), t("9:30"))
        self.assertEqual(self.p("09:30:00"), t("9:30"))
        self.assertEqual(self.p("9:30", base_date=date(2026, 10, 14)), t("9:30", day=-1))

    def test_prefer_past(self) -> None:
        # 0:10 に 23:50 と入力したら前日の 23:50
        self.assertEqual(parse_time("23:50", now=t("0:10")), t("23:50", day=-1))

    def test_day_start_hour(self) -> None:
        # 4時区切り: 15日 2:00 の時点の論理日付は 14日、"1:30" は 15日 1:30
        now = t("2:00")
        self.assertEqual(logical_date(now, 4), date(2026, 10, 14))
        self.assertEqual(parse_time("1:30", now=now, day_start_hour=4), t("1:30"))
        self.assertEqual(parse_time("23:00", now=now, day_start_hour=4), t("23:00", day=-1))
        self.assertEqual(day_range(date(2026, 10, 14), 4), (t("4:00", day=-1), t("4:00")))

    def test_with_date(self) -> None:
        self.assertEqual(self.p("2026-10-14 9:00"), t("9:00", day=-1))
        self.assertEqual(self.p("10/14 25:00"), t("1:00"))

    def test_invalid(self) -> None:
        for s in ("abc", "9", "-", "25:61"):
            with self.assertRaises(ActivityLogError):
                self.p(s)

    def test_parse_date(self) -> None:
        self.assertEqual(parse_date("today", self.today), self.today)
        self.assertEqual(parse_date("yesterday", self.today), date(2026, 10, 14))
        self.assertEqual(parse_date("-2", self.today), date(2026, 10, 13))
        self.assertEqual(parse_date("2026/1/2", self.today), date(2026, 1, 2))
        self.assertEqual(parse_date("10-1", self.today), date(2026, 10, 1))
        with self.assertRaises(ActivityLogError):
            parse_date("13/40", self.today)

    def test_fmt_duration(self) -> None:
        self.assertEqual(fmt_duration(90 * 60_000), "1:30")
        self.assertEqual(fmt_duration(90 * 60_000, "ja"), "1時間30分")
        self.assertEqual(fmt_duration(5 * 60_000, "ja"), "5分")


if __name__ == "__main__":
    unittest.main()
