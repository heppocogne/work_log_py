"""タスクの作成・切替・割り当ての整合性維持。

割り当て (assignments) のルール:
- 時間帯が重ならない。新しい範囲を登録したら、重なる既存の割り当てを切り詰め・分割する (_carve)
- 「今のタスク」は end_at IS NULL の1行のみ
- 書き込みはすべてトランザクション内で行う

CLI / GUI はタスクを「指定文字列」で渡せる (resolve):
- "案件A/手順書作成"  パス (存在しない階層は create=True のとき作成)
- "#12"              タスク ID
- "@1"               最近使ったタスクの番号 (1 が最新。GUI の 1〜9 キー選択に対応)
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass, field

from .errors import ActivityLogError, NotFoundError
from .models import Assignment, Task, UndoEntry
from .storage import Storage
from .timeutil import fmt_clock, now_ms

PATH_SEP = "/"
DISPLAY_SEP = " / "
# 未来の時刻をどこまで許すか (端末間の時計ずれ・入力の丸め誤差)
FUTURE_SLACK_MS = 60_000


@dataclass
class Resolved:
    task: Task
    created: list[str] = field(default_factory=list)  # 新しく作ったタスクのパス


def _recorded(label: str) -> Callable:
    """手動操作の前後で tasks / assignments を比べ、変わっていれば undo 履歴に積む。

    - 入れ子 (resume → switch など) は外側の操作として1件だけ記録する
    - 例外が出たら全体をロールバックする
    - ウィンドウ監視による自動の変更 (close_open_before) は対象にしない
    """
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(self: TaskService, *args, **kwargs):
            if self._recording:
                return fn(self, *args, **kwargs)
            self._recording = True
            try:
                with self.db.transaction():
                    before = self.db.snapshot()
                    result = fn(self, *args, **kwargs)
                    if self.db.snapshot() != before:
                        detail = " ".join(str(a) for a in args[:2] if isinstance(a, (str, int)))
                        self.db.push_undo(f"{label} {detail}".strip(), before, self.clock())
                    return result
            finally:
                self._recording = False
        return wrapper
    return deco


class TaskService:
    def __init__(self, storage: Storage, clock: Callable[[], int] = now_ms) -> None:
        self.db = storage
        self.clock = clock
        self._recording = False

    # =========================================================================
    # タスク
    # =========================================================================

    def _task_map(self) -> dict[int, Task]:
        return {t.id: t for t in self.db.all_tasks()}

    def _path(self, task_id: int, tasks: dict[int, Task]) -> str:
        names: list[str] = []
        t = tasks.get(task_id)
        while t is not None and t.parent_id is not None:
            names.append(t.name)
            t = tasks.get(t.parent_id)
        return DISPLAY_SEP.join(reversed(names))

    def list_tasks(self, include_archived: bool = False) -> list[Task]:
        """ルートを除く全タスク (パス順)。"""
        tasks = self._task_map()
        result = []
        for t in tasks.values():
            if t.parent_id is None:
                continue
            t.path = self._path(t.id, tasks)
            if include_archived or not self._archived_in_chain(t, tasks):
                result.append(t)
        result.sort(key=lambda t: t.path.split(DISPLAY_SEP))
        return result

    @staticmethod
    def _archived_in_chain(t: Task, tasks: dict[int, Task]) -> bool:
        cur: Task | None = t
        while cur is not None:
            if cur.archived:
                return True
            cur = tasks.get(cur.parent_id) if cur.parent_id is not None else None
        return False

    def get_task(self, task_id: int) -> Task:
        tasks = self._task_map()
        t = tasks.get(task_id)
        if t is None:
            raise NotFoundError(f"タスク #{task_id} が見つかりません")
        t.path = self._path(t.id, tasks)
        return t

    def with_path(self, a: Assignment) -> Assignment:
        a.task_path = self.get_task(a.task_id).path
        return a

    def recent_tasks(self, limit: int = 9) -> list[Task]:
        return [self.get_task(i) for i in self.db.recent_task_ids(limit)]

    @staticmethod
    def split_path(path: str) -> list[str]:
        parts = [p.strip() for p in path.split(PATH_SEP)]
        parts = [p for p in parts if p]
        if not parts:
            raise ActivityLogError("タスク名が空です")
        return parts

    @staticmethod
    def _validate_name(name: str) -> str:
        name = name.strip()
        if not name:
            raise ActivityLogError("タスク名が空です")
        if PATH_SEP in name:
            raise ActivityLogError(f"タスク名に '{PATH_SEP}' は使えません: {name}")
        if name[0] in "#@":
            raise ActivityLogError(f"タスク名を '#' や '@' で始めることはできません: {name}")
        return name

    def resolve(self, spec: str, create: bool = False) -> Resolved:
        spec = spec.strip()
        if spec.startswith("#") and spec[1:].isdigit():
            task = self.get_task(int(spec[1:]))
            if task.parent_id is None:
                raise ActivityLogError("ルートタスクは指定できません")
            return Resolved(task)
        if spec.startswith("@") and spec[1:].isdigit():
            n = int(spec[1:])
            recent = self.db.recent_task_ids(max(n, 1))
            if n < 1 or n > len(recent):
                raise NotFoundError(f"最近のタスク {spec} はありません (recent で一覧を確認できます)")
            return Resolved(self.get_task(recent[n - 1]))

        parts = self.split_path(spec)
        created: list[str] = []
        parent_id = self.db.root_id
        task: Task | None = None
        with self.db.transaction():
            for i, name in enumerate(parts):
                task = self.db.find_child_task(parent_id, name)
                if task is None:
                    if not create:
                        raise NotFoundError(
                            f"タスクが見つかりません: {DISPLAY_SEP.join(parts[: i + 1])}"
                        )
                    self._validate_name(name)
                    tid = self.db.insert_task(parent_id, name, self.clock())
                    created.append(DISPLAY_SEP.join(parts[: i + 1]))
                    task = self.db.get_task(tid)
                    assert task is not None
                parent_id = task.id
        assert task is not None
        return Resolved(self.get_task(task.id), created)

    @_recorded("task add")
    def create_task(self, path: str) -> Resolved:
        r = self.resolve(path, create=True)
        if not r.created:
            raise ActivityLogError(f"タスクは既に存在します: {r.task.path} (#{r.task.id})")
        return r

    @_recorded("task rename")
    def rename_task(self, spec: str, new_name: str) -> Task:
        task = self._editable(spec)
        new_name = self._validate_name(new_name)
        assert task.parent_id is not None
        other = self.db.find_child_task(task.parent_id, new_name)
        if other and other.id != task.id:
            raise ActivityLogError(f"同じ階層に同名のタスクがあります: #{other.id} (merge で統合できます)")
        self.db.update_task(task.id, name=new_name)
        return self.get_task(task.id)

    @_recorded("task move")
    def move_task(self, spec: str, new_parent_spec: str | None) -> Task:
        """new_parent_spec が None ならトップレベルへ移動する。"""
        task = self._editable(spec)
        if new_parent_spec is None:
            parent = self.get_task(self.db.root_id)
        else:
            parent = self.resolve(new_parent_spec).task
        tasks = self._task_map()
        cur: Task | None = tasks.get(parent.id)
        while cur is not None:  # 自分の子孫の下には移動できない
            if cur.id == task.id:
                raise ActivityLogError("自分自身や子孫の下には移動できません")
            cur = tasks.get(cur.parent_id) if cur.parent_id is not None else None
        if self.db.find_child_task(parent.id, task.name):
            raise ActivityLogError("移動先に同名のタスクがあります (merge で統合できます)")
        self.db.update_task(task.id, parent_id=parent.id)
        return self.get_task(task.id)

    @_recorded("task archive")
    def set_archived(self, spec: str, archived: bool) -> Task:
        task = self._editable(spec)
        self.db.update_task(task.id, archived=archived)
        return self.get_task(task.id)

    @_recorded("task merge")
    def merge_task(self, src_spec: str, dst_spec: str) -> tuple[Task, Task, int]:
        """src の割り当てをすべて dst に付け替え、src をアーカイブする。"""
        src = self._editable(src_spec)
        dst = self.resolve(dst_spec).task
        if src.id == dst.id:
            raise ActivityLogError("同じタスクです")
        with self.db.transaction():
            n = self.db.reassign_task(src.id, dst.id)
            self.db.update_task(src.id, archived=True)
        return self.get_task(src.id), dst, n

    def _editable(self, spec: str) -> Task:
        task = self.resolve(spec).task
        if task.parent_id is None:
            raise ActivityLogError("ルートタスクは変更できません")
        return task

    # =========================================================================
    # 割り当て
    # =========================================================================

    def current(self) -> Assignment | None:
        a = self.db.open_assignment()
        return self.with_path(a) if a else None

    def list_assignments(self, start: int | None, end: int | None) -> list[Assignment]:
        """[start, end) と重なる割り当て (切り詰めはしない)。"""
        tasks = self._task_map()
        result = self.db.overlapping_assignments(start, end)
        for a in result:
            a.task_path = self._path(a.task_id, tasks)
        return result

    def get_assignment(self, assignment_id: int) -> Assignment:
        a = self.db.get_assignment(assignment_id)
        if a is None:
            raise NotFoundError(f"割り当て {assignment_id} が見つかりません")
        return self.with_path(a)

    def _check_not_future(self, at: int, what: str = "時刻") -> None:
        if at > self.clock() + FUTURE_SLACK_MS:
            raise ActivityLogError(f"{what}が未来です: {fmt_clock(at)}")

    def _usable(self, task: Task) -> None:
        if task.parent_id is None:
            raise ActivityLogError("ルートタスクには割り当てられません")
        if self._archived_in_chain(task, self._task_map()):
            raise ActivityLogError(
                f"アーカイブ済みのタスクです: {task.path} (task unarchive で戻せます)"
            )

    @_recorded("start")
    def switch(self, task_spec: str, at: int | None = None, create: bool = True,
               source: str = "switch") -> tuple[Assignment, list[str]]:
        """タスクを開始 (切替) する。at 以降の既存の割り当ては切り詰める。"""
        at = self.clock() if at is None else at
        self._check_not_future(at, "開始時刻")
        resolved = self.resolve(task_spec, create=create)
        self._usable(resolved.task)
        with self.db.transaction():
            cur = self.db.open_assignment()
            if cur and cur.task_id == resolved.task.id and cur.start_at <= at:
                return self.with_path(cur), resolved.created  # 既に実行中
            self._carve(at, None)
            aid = self._insert_merged(resolved.task.id, at, None, source)
        return self.get_assignment(aid), resolved.created

    @_recorded("stop")
    def stop(self, at: int | None = None) -> Assignment:
        """進行中のタスクを at で終了する。"""
        at = self.clock() if at is None else at
        self._check_not_future(at, "終了時刻")
        with self.db.transaction():
            cur = self.db.open_assignment()
            if cur is None:
                raise ActivityLogError("進行中のタスクはありません")
            if at <= cur.start_at:
                raise ActivityLogError(
                    f"終了時刻 {fmt_clock(at)} が開始時刻 {fmt_clock(cur.start_at)} 以前です"
                    " (取り消すなら assign delete を使ってください)"
                )
            self.db.update_assignment(cur.id, cur.start_at, at)
        return self.get_assignment(cur.id)

    @_recorded("resume")
    def resume(self, at: int | None = None) -> tuple[Assignment, list[str]]:
        """直前のタスクを再開する。"""
        if self.db.open_assignment():
            raise ActivityLogError("既にタスクが進行中です")
        last = self.db.last_assignment()
        if last is None:
            raise ActivityLogError("再開できるタスクがありません")
        return self.switch(f"#{last.task_id}", at=at, create=False)

    @_recorded("assign add")
    def add_range(self, task_spec: str, start: int, end: int, create: bool = True,
                  source: str = "manual") -> tuple[Assignment, list[str]]:
        """[start, end) をタスクに割り当てる (さかのぼっての登録・空白の穴埋め)。"""
        if start >= end:
            raise ActivityLogError("開始時刻が終了時刻以降です")
        self._check_not_future(end, "終了時刻")
        resolved = self.resolve(task_spec, create=create)
        self._usable(resolved.task)
        with self.db.transaction():
            self._carve(start, end)
            aid = self._insert_merged(resolved.task.id, start, end, source)
        return self.get_assignment(aid), resolved.created

    def event_range(self, first_event_id: int, last_event_id: int) -> tuple[int, int]:
        """生データのイベント範囲 → 時刻の範囲 (最後のイベントの終わり = 次のイベントの開始)。"""
        first = self.db.get_raw_event(first_event_id)
        last = self.db.get_raw_event(last_event_id)
        if first is None or last is None:
            raise NotFoundError("イベントが見つかりません")
        if (first.started_at, first.id) > (last.started_at, last.id):
            first, last = last, first
        nxt = self.db.next_raw_event(last)
        end = nxt.started_at if nxt else self.clock()
        return first.started_at, end

    @_recorded("assign edit")
    def edit(self, assignment_id: int, *, start: int | None = None, end: int | None = None,
             task_spec: str | None = None, create: bool = True) -> tuple[Assignment, list[str]]:
        """割り当ての開始・終了・タスクを変更する。新しい範囲に重なる他の割り当ては切り詰める。"""
        a = self.get_assignment(assignment_id)
        new_start = a.start_at if start is None else start
        new_end = a.end_at if end is None else end
        created: list[str] = []
        task_id = a.task_id
        if task_spec is not None:
            resolved = self.resolve(task_spec, create=create)
            self._usable(resolved.task)
            task_id, created = resolved.task.id, resolved.created
        if new_end is not None:
            if new_start >= new_end:
                raise ActivityLogError("開始時刻が終了時刻以降になります")
            self._check_not_future(new_end, "終了時刻")
        else:
            self._check_not_future(new_start, "開始時刻")
        with self.db.transaction():
            self.db.delete_assignment(a.id)
            self._carve(new_start, new_end)
            self.db.insert_assignment(task_id, new_start, new_end, "manual", assignment_id=a.id)
        return self.get_assignment(a.id), created

    @_recorded("assign delete")
    def delete(self, assignment_id: int) -> Assignment:
        a = self.get_assignment(assignment_id)
        self.db.delete_assignment(a.id)
        return a

    def close_open_before(self, at: int) -> Assignment | None:
        """異常終了の補正: at より前に始まった進行中の割り当てを at で閉じる。"""
        with self.db.transaction():
            cur = self.db.open_assignment()
            if cur is None or cur.start_at >= at:
                return None
            self.db.update_assignment(cur.id, cur.start_at, at)
        return self.get_assignment(cur.id)

    # =========================================================================
    # 取り消し (undo)
    # =========================================================================

    def undo_history(self) -> list[UndoEntry]:
        """取り消せる操作 (新しい順)。"""
        return self.db.list_undo()

    def undo(self, undo_id: int | None = None) -> list[UndoEntry]:
        """直前の操作 (undo_id を指定したらその操作の前) まで戻す。取り消した操作を新しい順に返す。"""
        history = self.db.list_undo()
        if not history:
            raise ActivityLogError("取り消せる操作がありません")
        if undo_id is None:
            undo_id = history[0].id
        target = next((e for e in history if e.id == undo_id), None)
        if target is None:
            raise NotFoundError(f"取り消し履歴 {undo_id} が見つかりません (undo --list で確認できます)")
        with self.db.transaction():
            self.db.restore(target.snapshot)
            self.db.delete_undo_from(target.id)
        return [e for e in history if e.id >= target.id]

    # --- 内部 ----------------------------------------------------------------

    def _carve(self, start: int, end: int | None) -> None:
        """[start, end) と重なる既存の割り当てを切り詰め・分割・削除する。end=None は無限。"""
        inf = float("inf")
        e = inf if end is None else end
        for a in self.db.overlapping_assignments(start, end):
            a_end = inf if a.end_at is None else a.end_at
            if a.start_at < start and a_end > e:
                # 範囲が既存の割り当ての内側 → 前後に分割
                assert end is not None
                self.db.update_assignment(a.id, a.start_at, start)
                self.db.insert_assignment(a.task_id, end, a.end_at, a.source)
            elif a.start_at < start:
                self.db.update_assignment(a.id, a.start_at, start)  # 後ろを切る
            elif a_end > e:
                assert end is not None
                self.db.update_assignment(a.id, end, a.end_at)  # 前を切る
            else:
                self.db.delete_assignment(a.id)  # 完全に覆われる

    def _insert_merged(self, task_id: int, start: int, end: int | None, source: str) -> int:
        """挿入し、同じタスクで隙間なく接する割り当てがあれば1つにまとめる。"""
        prev = self.db.find_adjacent(task_id, ends_at=start)
        nxt = self.db.find_adjacent(task_id, starts_at=end) if end is not None else None
        if prev:
            new_end = nxt.end_at if nxt else end
            if nxt:
                self.db.delete_assignment(nxt.id)
            self.db.update_assignment(prev.id, prev.start_at, new_end)
            return prev.id
        if nxt:
            self.db.update_assignment(nxt.id, start, nxt.end_at)
            return nxt.id
        return self.db.insert_assignment(task_id, start, end, source)

