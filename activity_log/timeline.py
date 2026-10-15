"""生データの区間化と区間演算 (集計・日報・確認画面で共通に使う)。"""

from __future__ import annotations

from collections.abc import Iterable

from . import collector
from .models import Segment
from .settings import Settings
from .storage import Storage

Interval = tuple[int, int]


def raw_segments(storage: Storage, settings: Settings, start: int, end: int, now: int) -> list[Segment]:
    """[start, end) にかかる生データを区間にする。終了は次のイベントの開始時刻。

    最後のイベントの終了は、監視中なら現在時刻、そうでなければ最後の生存時刻で打ち切る。
    stop は「記録していない時間」なので区間に含めない。
    """
    events = storage.raw_events_between(start, end, include_preceding=True)
    nxt = storage.raw_events_between(end, None)[:1] if events else []
    st = collector.status(storage, settings, now)
    tail_end = now if st.running else (st.heartbeat_at or 0)
    segs: list[Segment] = []
    for i, ev in enumerate(events):
        if i + 1 < len(events):
            seg_end = events[i + 1].started_at
        elif nxt:
            seg_end = nxt[0].started_at
        else:
            seg_end = max(tail_end, ev.started_at)
        if ev.kind == "stop":
            continue
        s, e = max(ev.started_at, start), min(seg_end, end, now)
        if e > s:
            segs.append(Segment(ev, s, e))
    return segs


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    result: list[Interval] = []
    for s, e in sorted(i for i in intervals if i[1] > i[0]):
        if result and s <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], e))
        else:
            result.append((s, e))
    return result


def total(intervals: Iterable[Interval]) -> int:
    return sum(e - s for s, e in merge(intervals))


def intersect_total(a: Iterable[Interval], b: Iterable[Interval]) -> int:
    ma, mb = merge(a), merge(b)
    i = j = 0
    acc = 0
    while i < len(ma) and j < len(mb):
        s, e = max(ma[i][0], mb[j][0]), min(ma[i][1], mb[j][1])
        if e > s:
            acc += e - s
        if ma[i][1] < mb[j][1]:
            i += 1
        else:
            j += 1
    return acc


def gaps(intervals: Iterable[Interval], start: int, end: int) -> list[Interval]:
    """[start, end) のうち intervals に含まれない部分。"""
    result: list[Interval] = []
    cur = start
    for s, e in merge(intervals):
        if s > cur:
            result.append((cur, min(s, end)))
        cur = max(cur, e)
        if cur >= end:
            break
    if cur < end:
        result.append((cur, end))
    return [g for g in result if g[1] > g[0]]
