"""コア層が返すデータ型。CLI / GUI どちらからも扱えるよう、すべて素朴な dataclass にする。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .timeutil import iso


@dataclass
class Task:
    id: int
    parent_id: int | None
    name: str
    archived: bool
    created_at: int
    path: str = ""  # 表示用のフルパス ("案件A / 手順書作成")。ルートは ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at_iso"] = iso(self.created_at)
        return d


@dataclass
class Assignment:
    id: int
    task_id: int
    start_at: int
    end_at: int | None  # None = 進行中
    source: str  # switch / manual
    task_path: str = ""

    @property
    def is_open(self) -> bool:
        return self.end_at is None

    def duration(self, now: int) -> int:
        return (self.end_at if self.end_at is not None else now) - self.start_at

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start_at_iso"] = iso(self.start_at)
        d["end_at_iso"] = iso(self.end_at)
        return d


@dataclass
class RawEvent:
    id: int
    started_at: int
    kind: str  # window / idle / stop
    process: str | None
    title: str | None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["started_at_iso"] = iso(self.started_at)
        return d


@dataclass
class Segment:
    """生データの1区間 (終了時刻を次のイベントから補ったもの)。"""

    event: RawEvent
    start: int
    end: int

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass
class CollectorStatus:
    running: bool
    pid: int | None
    started_at: int | None
    heartbeat_at: int | None
    last_event: RawEvent | None = None
    idle: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "pid": self.pid,
            "started_at": self.started_at,
            "started_at_iso": iso(self.started_at),
            "heartbeat_at": self.heartbeat_at,
            "heartbeat_at_iso": iso(self.heartbeat_at),
            "idle": self.idle,
            "last_event": self.last_event.to_dict() if self.last_event else None,
        }


@dataclass
class TaskTotal:
    task: Task
    own_ms: int = 0  # このタスクに直接割り当てた時間
    total_ms: int = 0  # 子孫を含む合計
    children: list[TaskTotal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task.id,
            "name": self.task.name,
            "path": self.task.path,
            "own_ms": self.own_ms,
            "total_ms": self.total_ms,
            "children": [c.to_dict() for c in self.children],
        }


@dataclass
class UndoEntry:
    id: int
    created_at: int
    label: str  # 取り消す操作の説明 ("start 案件A")
    snapshot: str = field(repr=False)  # 操作前の tasks / assignments (JSON)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "created_at": self.created_at,
                "created_at_iso": iso(self.created_at), "label": self.label}
