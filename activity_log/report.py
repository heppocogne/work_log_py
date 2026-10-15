"""1日分の集計と日報 (Markdown) の作成。

build_daily() が集計結果 (DailySummary) を返し、render_markdown() が文字列にする。
GUI の確認画面も build_daily() の結果をそのまま表示に使える。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from . import timeline, title_parser
from .models import Assignment, Segment, Task, TaskTotal
from .settings import Settings
from .storage import Storage
from .task_service import TaskService
from .timeutil import day_range, fmt_clock_on, fmt_date, fmt_duration, iso


@dataclass
class TimelineRow:
    start: int
    end: int
    assignment: Assignment | None  # None = 未割当の空白

    @property
    def duration(self) -> int:
        return self.end - self.start


@dataclass
class WindowTotal:
    app: str
    label: str
    ms: int


@dataclass
class DailySummary:
    date: date
    start: int
    end: int
    now: int
    assignments: list[Assignment]  # その日にかかる割り当て (元の時刻のまま)
    timeline: list[TimelineRow]  # その日の範囲に切り詰めた割り当て + 空白
    task_totals: list[TaskTotal]  # ツリー (トップレベルから)
    assigned_ms: int
    active_ms: int  # PC を操作していた時間 (window の区間の合計)
    idle_ms: int
    active_unassigned_ms: int  # 操作していたがタスク未割当の時間
    first_activity: int | None
    last_activity: int | None
    windows: list[WindowTotal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "range": {"start": self.start, "end": self.end,
                      "start_iso": iso(self.start), "end_iso": iso(self.end)},
            "assigned_ms": self.assigned_ms,
            "active_ms": self.active_ms,
            "idle_ms": self.idle_ms,
            "active_unassigned_ms": self.active_unassigned_ms,
            "first_activity_iso": iso(self.first_activity),
            "last_activity_iso": iso(self.last_activity),
            "task_totals": [t.to_dict() for t in self.task_totals],
            "timeline": [
                {
                    "start_iso": iso(r.start), "end_iso": iso(r.end), "duration_ms": r.duration,
                    "assignment_id": r.assignment.id if r.assignment else None,
                    "task_path": r.assignment.task_path if r.assignment else None,
                }
                for r in self.timeline
            ],
            "windows": [w.__dict__ for w in self.windows],
        }


def build_daily(storage: Storage, tasks: TaskService, settings: Settings,
                d: date, now: int, min_gap_ms: int = 60_000) -> DailySummary:
    lo, hi = day_range(d, settings.day_start_hour)
    hi_eff = max(lo, min(hi, now))

    assigns = tasks.list_assignments(lo, hi)
    rows: list[TimelineRow] = []
    own: dict[int, int] = {}
    intervals: list[timeline.Interval] = []
    for a in assigns:
        s, e = max(a.start_at, lo), min(a.end_at if a.end_at is not None else now, hi)
        if e <= s:
            continue
        rows.append(TimelineRow(s, e, a))
        own[a.task_id] = own.get(a.task_id, 0) + (e - s)
        intervals.append((s, e))

    segs = timeline.raw_segments(storage, settings, lo, hi, now)
    active = [(s.start, s.end) for s in segs if s.event.kind == "window"]
    idle = [(s.start, s.end) for s in segs if s.event.kind == "idle"]

    # 空白 (未割当) の行を挟む。PC を使っていた時間帯の中だけを対象にする
    if active:
        spans = active + intervals
        span_lo, span_hi = min(s for s, _ in spans), max(e for _, e in spans)
        for g0, g1 in timeline.gaps(intervals, span_lo, span_hi):
            if g1 - g0 >= min_gap_ms and timeline.intersect_total([(g0, g1)], active) >= min_gap_ms:
                rows.append(TimelineRow(g0, g1, None))
    rows.sort(key=lambda r: r.start)

    return DailySummary(
        date=d,
        start=lo,
        end=hi_eff,
        now=now,
        assignments=assigns,
        timeline=rows,
        task_totals=_tree_totals(tasks, own),
        assigned_ms=timeline.total(intervals),
        active_ms=timeline.total(active),
        idle_ms=timeline.total(idle),
        active_unassigned_ms=timeline.total(active) - timeline.intersect_total(active, intervals),
        first_activity=min((s for s, _ in active), default=None),
        last_activity=max((e for _, e in active), default=None),
        windows=_window_totals(segs),
    )


def _tree_totals(tasks: TaskService, own: dict[int, int]) -> list[TaskTotal]:
    all_tasks = {t.id: t for t in tasks.list_tasks(include_archived=True)}
    nodes: dict[int, TaskTotal] = {}

    def node(t: Task) -> TaskTotal:
        if t.id not in nodes:
            nodes[t.id] = TaskTotal(t)
        return nodes[t.id]

    roots: list[TaskTotal] = []
    for tid, ms in own.items():
        t = all_tasks.get(tid)
        if t is None:
            continue
        n = node(t)
        n.own_ms += ms
        # 祖先に合計を足していく
        cur: Task | None = t
        while cur is not None:
            cn = node(cur)
            cn.total_ms += ms
            parent = all_tasks.get(cur.parent_id) if cur.parent_id is not None else None
            if parent is not None:
                pn = node(parent)
                if cn not in pn.children:
                    pn.children.append(cn)
            elif cn not in roots:
                roots.append(cn)
            cur = parent

    def sort(ns: list[TaskTotal]) -> list[TaskTotal]:
        ns.sort(key=lambda n: -n.total_ms)
        for n in ns:
            sort(n.children)
        return ns

    return sort(roots)


def _window_totals(segs: list[Segment]) -> list[WindowTotal]:
    acc: dict[tuple[str, str], int] = {}
    for s in segs:
        if s.event.kind != "window":
            continue
        p = title_parser.parse(s.event.process, s.event.title)
        acc[(p.app, p.label)] = acc.get((p.app, p.label), 0) + s.duration
    items = [WindowTotal(app, label, ms) for (app, label), ms in acc.items()]
    items.sort(key=lambda w: -w.ms)
    return items


# --- Markdown ----------------------------------------------------------------


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|")


def render_markdown(summary: DailySummary, settings: Settings, top_windows: int = 10,
                    include_windows: bool = True) -> str:
    d = summary.date
    dsh = settings.day_start_hour
    out: list[str] = [f"# 日報 {fmt_date(d)}", ""]

    out += ["## 概要", ""]
    if summary.first_activity is not None:
        out.append(
            f"- PC 使用: {fmt_clock_on(summary.first_activity, d, dsh)}〜"
            f"{fmt_clock_on(summary.last_activity, d, dsh)}"
            f" (操作 {fmt_duration(summary.active_ms, 'ja')} / 離席 {fmt_duration(summary.idle_ms, 'ja')})"
        )
    out.append(f"- 記録したタスク時間: {fmt_duration(summary.assigned_ms, 'ja')}")
    if summary.active_unassigned_ms >= 60_000:
        out.append(f"- 未割当 (PC 操作中): {fmt_duration(summary.active_unassigned_ms, 'ja')}")
    out.append("")

    out += ["## タスク別", ""]
    if summary.task_totals:
        def walk(n: TaskTotal, depth: int) -> None:
            extra = ""
            if n.children and n.own_ms:
                extra = f" (うち直接 {fmt_duration(n.own_ms, 'ja')})"
            out.append(f"{'  ' * depth}- {n.task.name}: {fmt_duration(n.total_ms, 'ja')}{extra}")
            for c in n.children:
                walk(c, depth + 1)

        for n in summary.task_totals:
            walk(n, 0)
    else:
        out.append("(記録なし)")
    out.append("")

    out += ["## タイムライン", ""]
    if summary.timeline:
        out += ["| 開始 | 終了 | 時間 | タスク |", "|---|---|---|---|"]
        for r in summary.timeline:
            ongoing = r.assignment is not None and r.assignment.end_at is None and r.end >= summary.now
            end = "(進行中)" if ongoing else fmt_clock_on(r.end, d, dsh)
            name = _md_escape(r.assignment.task_path) if r.assignment else "*(未割当)*"
            out.append(f"| {fmt_clock_on(r.start, d, dsh)} | {end} | {fmt_duration(r.duration)} | {name} |")
    else:
        out.append("(記録なし)")
    out.append("")

    if include_windows and summary.windows:
        out += [f"## よく使ったウィンドウ (上位 {top_windows})", ""]
        out += ["| アプリ | 内容 | 時間 |", "|---|---|---|"]
        for w in summary.windows[:top_windows]:
            out.append(f"| {_md_escape(w.app)} | {_md_escape(w.label)} | {fmt_duration(w.ms)} |")
        out.append("")

    out += ["## メモ", "", "- ", ""]
    return "\n".join(out)
