"""SQLite アクセスとスキーマ管理。

- 接続は1本を使い回す (GUI ではメインスレッドのみで使用)
- CLI と監視プロセスが同じ DB を同時に触るため、WAL + busy_timeout を設定する
- 整合性 (割り当ての重なり防止など) は task_service.py が持つ。ここは素朴な読み書きだけ
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .models import Assignment, RawEvent, Task
from .timeutil import now_ms

SCHEMA_VERSION = 1
ROOT_NAME = "(root)"
UNCATEGORIZED_NAME = "未分類"

_SCHEMA_V1 = """
CREATE TABLE tasks (
  id          INTEGER PRIMARY KEY,
  parent_id   INTEGER REFERENCES tasks(id),
  name        TEXT NOT NULL,
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  INTEGER NOT NULL,
  UNIQUE(parent_id, name)
);

CREATE TABLE raw_events (
  id          INTEGER PRIMARY KEY,
  started_at  INTEGER NOT NULL,
  kind        TEXT NOT NULL,
  process     TEXT,
  title       TEXT
);
CREATE INDEX idx_raw_started ON raw_events(started_at);

CREATE TABLE assignments (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER NOT NULL REFERENCES tasks(id),
  start_at    INTEGER NOT NULL,
  end_at      INTEGER,
  source      TEXT NOT NULL
);
CREATE INDEX idx_assign_start ON assignments(start_at);

CREATE TABLE app_state (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, path: str | Path) -> None:
        path = str(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # isolation_level=None: 自動コミット。まとめて書くときは transaction() を使う
        self.conn = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 10000")
        self._migrate()
        self.root_id = self._root_id()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self.conn.in_transaction:  # 入れ子は外側にまとめる
            yield
            return
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    # --- スキーマ ------------------------------------------------------------

    def _migrate(self) -> None:
        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"DB のスキーマ ({version}) がこのバージョンより新しいです")
        if version == SCHEMA_VERSION:
            return
        with self.transaction():
            if version < 1:
                # executescript は暗黙に COMMIT するので1文ずつ実行する
                for stmt in _SCHEMA_V1.split(";"):
                    if stmt.strip():
                        self.conn.execute(stmt)
                now = now_ms()
                cur = self.conn.execute(
                    "INSERT INTO tasks(parent_id, name, created_at) VALUES (NULL, ?, ?)",
                    (ROOT_NAME, now),
                )
                self.conn.execute(
                    "INSERT INTO tasks(parent_id, name, created_at) VALUES (?, ?, ?)",
                    (cur.lastrowid, UNCATEGORIZED_NAME, now),
                )
            # 将来: if version < 2: ...
            self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _root_id(self) -> int:
        row = self.conn.execute(
            "SELECT id FROM tasks WHERE parent_id IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        return int(row["id"])

    # --- tasks ---------------------------------------------------------------

    @staticmethod
    def _task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            parent_id=row["parent_id"],
            name=row["name"],
            archived=bool(row["archived"]),
            created_at=row["created_at"],
        )

    def get_task(self, task_id: int) -> Task | None:
        row = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task(row) if row else None

    def find_child_task(self, parent_id: int, name: str) -> Task | None:
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE parent_id = ? AND name = ?", (parent_id, name)
        ).fetchone()
        return self._task(row) if row else None

    def all_tasks(self) -> list[Task]:
        rows = self.conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
        return [self._task(r) for r in rows]

    def insert_task(self, parent_id: int, name: str, created_at: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO tasks(parent_id, name, created_at) VALUES (?, ?, ?)",
            (parent_id, name, created_at),
        )
        return int(cur.lastrowid)

    def update_task(self, task_id: int, *, name: str | None = None,
                    parent_id: int | None = None, archived: bool | None = None) -> None:
        if name is not None:
            self.conn.execute("UPDATE tasks SET name = ? WHERE id = ?", (name, task_id))
        if parent_id is not None:
            self.conn.execute("UPDATE tasks SET parent_id = ? WHERE id = ?", (parent_id, task_id))
        if archived is not None:
            self.conn.execute("UPDATE tasks SET archived = ? WHERE id = ?", (int(archived), task_id))

    def count_assignments_of(self, task_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE task_id = ?", (task_id,)
        ).fetchone()[0]

    def reassign_task(self, from_id: int, to_id: int) -> int:
        cur = self.conn.execute(
            "UPDATE assignments SET task_id = ? WHERE task_id = ?", (to_id, from_id)
        )
        return cur.rowcount

    # --- assignments ---------------------------------------------------------

    @staticmethod
    def _assignment(row: sqlite3.Row) -> Assignment:
        return Assignment(
            id=row["id"],
            task_id=row["task_id"],
            start_at=row["start_at"],
            end_at=row["end_at"],
            source=row["source"],
        )

    def get_assignment(self, assignment_id: int) -> Assignment | None:
        row = self.conn.execute(
            "SELECT * FROM assignments WHERE id = ?", (assignment_id,)
        ).fetchone()
        return self._assignment(row) if row else None

    def open_assignment(self) -> Assignment | None:
        row = self.conn.execute(
            "SELECT * FROM assignments WHERE end_at IS NULL ORDER BY start_at DESC LIMIT 1"
        ).fetchone()
        return self._assignment(row) if row else None

    def last_assignment(self) -> Assignment | None:
        row = self.conn.execute(
            "SELECT * FROM assignments ORDER BY start_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return self._assignment(row) if row else None

    def recent_assignments(self, limit: int) -> list[Assignment]:
        rows = self.conn.execute(
            "SELECT * FROM assignments ORDER BY start_at DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._assignment(r) for r in rows]

    def overlapping_assignments(self, start: int | None, end: int | None) -> list[Assignment]:
        """[start, end) と重なる割り当て。None は無限 (start=None なら最初から、end=None なら以降すべて)。"""
        sql = "SELECT * FROM assignments WHERE 1=1"
        params: list[int] = []
        if end is not None:
            sql += " AND start_at < ?"
            params.append(end)
        if start is not None:
            sql += " AND (end_at IS NULL OR end_at > ?)"
            params.append(start)
        sql += " ORDER BY start_at, id"
        return [self._assignment(r) for r in self.conn.execute(sql, params)]

    def insert_assignment(self, task_id: int, start_at: int, end_at: int | None,
                          source: str, assignment_id: int | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO assignments(id, task_id, start_at, end_at, source) VALUES (?, ?, ?, ?, ?)",
            (assignment_id, task_id, start_at, end_at, source),
        )
        return int(cur.lastrowid)

    def update_assignment(self, assignment_id: int, start_at: int, end_at: int | None) -> None:
        self.conn.execute(
            "UPDATE assignments SET start_at = ?, end_at = ? WHERE id = ?",
            (start_at, end_at, assignment_id),
        )

    def delete_assignment(self, assignment_id: int) -> None:
        self.conn.execute("DELETE FROM assignments WHERE id = ?", (assignment_id,))

    def find_adjacent(self, task_id: int, *, ends_at: int | None = None,
                      starts_at: int | None = None) -> Assignment | None:
        if ends_at is not None:
            row = self.conn.execute(
                "SELECT * FROM assignments WHERE task_id = ? AND end_at = ?", (task_id, ends_at)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM assignments WHERE task_id = ? AND start_at = ?", (task_id, starts_at)
            ).fetchone()
        return self._assignment(row) if row else None

    def recent_task_ids(self, limit: int) -> list[int]:
        rows = self.conn.execute(
            """
            SELECT a.task_id FROM assignments a JOIN tasks t ON t.id = a.task_id
            WHERE t.archived = 0
            GROUP BY a.task_id ORDER BY MAX(a.start_at) DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]

    # --- raw_events (追記のみ) -----------------------------------------------

    @staticmethod
    def _raw(row: sqlite3.Row) -> RawEvent:
        return RawEvent(
            id=row["id"],
            started_at=row["started_at"],
            kind=row["kind"],
            process=row["process"],
            title=row["title"],
        )

    def add_raw_event(self, started_at: int, kind: str,
                      process: str | None = None, title: str | None = None) -> RawEvent:
        cur = self.conn.execute(
            "INSERT INTO raw_events(started_at, kind, process, title) VALUES (?, ?, ?, ?)",
            (started_at, kind, process, title),
        )
        return RawEvent(int(cur.lastrowid), started_at, kind, process, title)

    def get_raw_event(self, event_id: int) -> RawEvent | None:
        row = self.conn.execute("SELECT * FROM raw_events WHERE id = ?", (event_id,)).fetchone()
        return self._raw(row) if row else None

    def last_raw_event(self) -> RawEvent | None:
        row = self.conn.execute(
            "SELECT * FROM raw_events ORDER BY started_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return self._raw(row) if row else None

    def next_raw_event(self, event: RawEvent) -> RawEvent | None:
        row = self.conn.execute(
            """
            SELECT * FROM raw_events
            WHERE started_at > ? OR (started_at = ? AND id > ?)
            ORDER BY started_at, id LIMIT 1
            """,
            (event.started_at, event.started_at, event.id),
        ).fetchone()
        return self._raw(row) if row else None

    def raw_events_between(self, start: int | None, end: int | None,
                           include_preceding: bool = False) -> list[RawEvent]:
        """started_at が [start, end) のイベント。include_preceding で start 直前の1件も含める。"""
        sql = "SELECT * FROM raw_events WHERE 1=1"
        params: list[int] = []
        if start is not None:
            sql += " AND started_at >= ?"
            params.append(start)
        if end is not None:
            sql += " AND started_at < ?"
            params.append(end)
        sql += " ORDER BY started_at, id"
        events = [self._raw(r) for r in self.conn.execute(sql, params)]
        if include_preceding and start is not None:
            row = self.conn.execute(
                "SELECT * FROM raw_events WHERE started_at < ? ORDER BY started_at DESC, id DESC LIMIT 1",
                (start,),
            ).fetchone()
            if row:
                events.insert(0, self._raw(row))
        return events

    # --- app_state -----------------------------------------------------------

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def get_state_int(self, key: str) -> int | None:
        v = self.get_state(key)
        try:
            return int(v) if v is not None else None
        except ValueError:
            return None

    def set_state(self, key: str, value: str | int) -> None:
        self.conn.execute(
            "INSERT INTO app_state(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def delete_state(self, key: str) -> None:
        self.conn.execute("DELETE FROM app_state WHERE key = ?", (key,))
