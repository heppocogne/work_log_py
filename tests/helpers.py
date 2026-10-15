from __future__ import annotations

from datetime import datetime

from activity_log.app import App
from activity_log.settings import Settings
from activity_log.timeutil import from_dt

BASE = datetime(2026, 10, 15)


def t(hhmm: str, day: int = 0) -> int:
    """2026-10-15 (+day) の HH:MM をミリ秒にする。"""
    h, m = map(int, hhmm.split(":"))
    return from_dt(BASE.replace(hour=h, minute=m)) + day * 86_400_000


class Clock:
    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms

    def set(self, hhmm: str) -> None:
        self.ms = t(hhmm)

    def advance(self, seconds: float) -> None:
        self.ms += int(seconds * 1000)


def make_app(now: str = "18:00", **settings: object) -> tuple[App, Clock]:
    clock = Clock(t(now))
    app = App(Settings(**settings), ":memory:", clock=clock)  # type: ignore[arg-type]
    return app, clock
