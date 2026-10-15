"""DB の内容を JSON 用の dict にする。時刻は UNIX ミリ秒と ISO 8601 (ローカル時刻) の両方を出す。"""

from __future__ import annotations

from typing import Any

from . import __version__, title_parser
from .storage import SCHEMA_VERSION, Storage
from .task_service import TaskService
from .timeutil import iso

TABLES = ("tasks", "assignments", "raw_events", "app_state")


def export(storage: Storage, tasks: TaskService, *, start: int | None = None,
           end: int | None = None, tables: tuple[str, ...] = TABLES[:3],
           now: int) -> dict[str, Any]:
    """[start, end) にかかるデータを書き出す (tasks と app_state は期間に関係なく全件)。"""
    data: dict[str, Any] = {
        "format": "activity-log-export",
        "app_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "exported_at": now,
        "exported_at_iso": iso(now),
        "range": {"start": start, "end": end, "start_iso": iso(start), "end_iso": iso(end)},
    }
    if "tasks" in tables:
        data["tasks"] = [t.to_dict() for t in tasks.list_tasks(include_archived=True)]
    if "assignments" in tables:
        data["assignments"] = [a.to_dict() for a in tasks.list_assignments(start, end)]
    if "raw_events" in tables:
        rows = []
        for e in storage.raw_events_between(start, end):
            d = e.to_dict()
            if e.kind == "window":
                p = title_parser.parse(e.process, e.title)
                d["app"], d["label"] = p.app, p.label
            rows.append(d)
        data["raw_events"] = rows
    if "app_state" in tables:
        data["app_state"] = {
            r["key"]: r["value"] for r in storage.conn.execute("SELECT key, value FROM app_state")
        }
    return data
