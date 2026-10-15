"""コア層をまとめる入口。CLI と GUI はどちらも App を作って使う。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from . import settings as settings_mod
from .settings import Settings
from .storage import Storage
from .task_service import TaskService
from .timeutil import logical_date, now_ms, parse_date, parse_time


class App:
    def __init__(self, settings: Settings, db_path: Path | str | None = None,
                 clock: Callable[[], int] = now_ms) -> None:
        self.settings = settings
        self.clock = clock
        self.storage = Storage(db_path if db_path is not None else settings.db_file)
        self.tasks = TaskService(self.storage, clock)

    @classmethod
    def open(cls, config_path: Path | None = None, db_path: Path | str | None = None) -> App:
        return cls(settings_mod.load(config_path), db_path)

    def close(self) -> None:
        self.storage.close()

    def now(self) -> int:
        return self.clock()

    def today(self) -> date:
        return logical_date(self.clock(), self.settings.day_start_hour)

    def date_of(self, ms: int) -> date:
        return logical_date(ms, self.settings.day_start_hour)

    def parse_date(self, text: str | None) -> date:
        return parse_date(text or "today", self.today())

    def parse_time(self, text: str, base_date: date | None = None) -> int:
        return parse_time(text, now=self.clock(), base_date=base_date,
                          day_start_hour=self.settings.day_start_hour)
