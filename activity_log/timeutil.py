"""時刻ユーティリティ。

DB 上の時刻はすべて UNIX ミリ秒 (UTC)。表示・入力時にだけローカル時刻へ変換する。
「論理日付」は day_start_hour 時を日付の区切りとした日付 (深夜作業を前日扱いにするため)。
"""

from __future__ import annotations

import re
import time
from datetime import date, datetime, time as dtime, timedelta

from .errors import ActivityLogError

WEEKDAYS_JA = "月火水木金土日"


def now_ms() -> int:
    return int(time.time() * 1000)


def to_dt(ms: int) -> datetime:
    """UNIX ミリ秒 → ローカル時刻 (naive)。"""
    return datetime.fromtimestamp(ms / 1000)


def from_dt(dt: datetime) -> int:
    """ローカル時刻 (naive) → UNIX ミリ秒。"""
    return int(dt.timestamp() * 1000)


def iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="seconds")


def logical_date(ms: int, day_start_hour: int = 0) -> date:
    return (to_dt(ms) - timedelta(hours=day_start_hour)).date()


def day_range(d: date, day_start_hour: int = 0) -> tuple[int, int]:
    """論理日付 d の [開始, 終了) を UNIX ミリ秒で返す。"""
    start = datetime.combine(d, dtime(hour=day_start_hour))
    return from_dt(start), from_dt(start + timedelta(days=1))


def fmt_clock(ms: int | None, with_seconds: bool = False) -> str:
    if ms is None:
        return "--:--"
    return to_dt(ms).strftime("%H:%M:%S" if with_seconds else "%H:%M")


def fmt_clock_on(ms: int | None, base: date, day_start_hour: int = 0) -> str:
    """基準日以外の時刻には日付を付けて表示する。"""
    if ms is None:
        return "(進行中)"
    if logical_date(ms, day_start_hour) == base:
        return fmt_clock(ms)
    return to_dt(ms).strftime("%m/%d %H:%M")


def fmt_date(d: date) -> str:
    return f"{d.isoformat()} ({WEEKDAYS_JA[d.weekday()]})"


def fmt_duration(ms: int, style: str = "short") -> str:
    """short: "1:05"  ja: "1時間05分" / "5分"。"""
    minutes = (max(0, ms) + 30_000) // 60_000  # 分単位に四捨五入
    h, m = divmod(minutes, 60)
    if style == "ja":
        return f"{h}時間{m:02d}分" if h else f"{m}分"
    return f"{h}:{m:02d}"


# --- 入力の解釈 -------------------------------------------------------------

_DATE_FULL = re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})")
_DATE_SHORT = re.compile(r"(\d{1,2})[-/](\d{1,2})")
_REL_DAYS = re.compile(r"[-+]\d+")
_CLOCK = re.compile(r"(\d{1,2}):(\d{2})(?::(\d{2}))?")
_REL_TIME = re.compile(r"-(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")
_REL_TIME_JA = re.compile(r"(?:(\d+)時間)?(?:(\d+)分)?前")


def parse_date(text: str, today: date) -> date:
    """日付指定を解釈する。

    today / yesterday / 今日 / 昨日 / -1 (n日前) / +1 / 2026-10-15 / 10-15 / 10/15
    """
    s = text.strip().lower()
    if s in ("", "today", "今日"):
        return today
    if s in ("yesterday", "昨日"):
        return today - timedelta(days=1)
    if _REL_DAYS.fullmatch(s):
        return today + timedelta(days=int(s))
    try:
        if m := _DATE_FULL.fullmatch(s):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _DATE_SHORT.fullmatch(s):
            return date(today.year, int(m[1]), int(m[2]))
    except ValueError:
        pass
    raise ActivityLogError(f"日付を解釈できません: {text!r} (例: today, yesterday, -2, 2026-10-15, 10/15)")


def parse_time(
    text: str,
    *,
    now: int,
    base_date: date | None = None,
    day_start_hour: int = 0,
    prefer_past: bool = True,
) -> int:
    """時刻指定を解釈して UNIX ミリ秒を返す。

    - now
    - -15m / -1h30m / -90s / 15分前 / 1時間前  (現在からさかのぼる)
    - 9:30 / 09:30:15 / 25:10  (base_date の論理日付上の時刻。day_start_hour より前の時刻は翌日扱い)
    - 2026-10-15 9:30 / 10/15 9:30  (日付付き)

    prefer_past=True で日付を省略した結果が未来になった場合は前日とみなす
    (0:10 に「23:50 から開始」と入力したときなど)。
    """
    s = text.strip()
    if s.lower() in ("now", "今"):
        return now
    if s.startswith("-") and (m := _REL_TIME.fullmatch(s)) and any(m.groups()):
        h, mi, se = (int(x or 0) for x in m.groups())
        return now - ((h * 60 + mi) * 60 + se) * 1000
    if (m := _REL_TIME_JA.fullmatch(s)) and any(m.groups()):
        h, mi = (int(x or 0) for x in m.groups())
        return now - (h * 60 + mi) * 60_000

    today = logical_date(now, day_start_hour)
    parts = s.replace("T", " ").split()
    if len(parts) == 2:
        d = parse_date(parts[0], today)
        return _combine(d, parts[1], text, shift_hour=0)
    if len(parts) == 1 and _CLOCK.fullmatch(parts[0]):
        d = base_date or today
        ms = _combine(d, parts[0], text, shift_hour=day_start_hour)
        if prefer_past and base_date is None and ms > now + 60_000:
            ms = _combine(d - timedelta(days=1), parts[0], text, shift_hour=day_start_hour)
        return ms
    raise ActivityLogError(
        f"時刻を解釈できません: {text!r} (例: now, 9:30, -15m, 15分前, 2026-10-15 9:30)"
    )


def _combine(d: date, clock: str, original: str, shift_hour: int) -> int:
    m = _CLOCK.fullmatch(clock)
    if not m:
        raise ActivityLogError(f"時刻を解釈できません: {original!r}")
    h, mi, se = int(m[1]), int(m[2]), int(m[3] or 0)
    if h >= 48 or mi >= 60 or se >= 60:
        raise ActivityLogError(f"時刻が範囲外です: {original!r}")
    days = 0
    if h >= 24:
        days, h = 1, h - 24
    elif h < shift_hour:
        days = 1  # 論理日付の区切りより前 = 暦の上では翌日
    dt = datetime.combine(d + timedelta(days=days), dtime(h, mi, se))
    return from_dt(dt)
