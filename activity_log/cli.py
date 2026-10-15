"""コマンドラインインターフェイス。

コア層 (App / TaskService / Collector / report / export) を呼び出して表示するだけの薄い層。
一覧・状態系のコマンドは --json で機械可読な出力ができる (GUI や他ツールとの連携用)。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from . import __version__, collector, export as export_mod, report, settings as settings_mod
from . import timeline, title_parser, winapi
from .app import App
from .errors import ActivityLogError
from .models import Assignment, RawEvent, Task
from .timeutil import day_range, fmt_clock, fmt_clock_on, fmt_date, fmt_duration, to_dt

TASK_SPEC_HELP = 'タスク: パス ("案件A/手順書作成")、ID ("#12")、最近の番号 ("@1")'
TIME_HELP = "時刻: now, 9:30, -15m, 15分前, 2026-10-15 9:30 など"


# =============================================================================
# 出力ヘルパー
# =============================================================================


def out(text: str = "") -> None:
    print(text)


def width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def ljust(s: str, n: int) -> str:
    """全角文字を2桁として左寄せする。"""
    return s + " " * max(0, n - width(s))


def rjust(s: str, n: int) -> str:
    return " " * max(0, n - width(s)) + s


def emit_json(obj: Any) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def warn(text: str) -> None:
    print(text, file=sys.stderr)


def fmt_assignment(a: Assignment, now: int, base: date | None = None, dsh: int = 0) -> str:
    if base is None:
        span = f"{fmt_clock(a.start_at)}〜{fmt_clock(a.end_at) if a.end_at else ''}"
    else:
        span = f"{fmt_clock_on(a.start_at, base, dsh)}〜{fmt_clock_on(a.end_at, base, dsh) if a.end_at else ''}"
    return f"{a.task_path} (#{a.task_id})  {span}  [{fmt_duration(a.duration(now))}]"


def print_created(created: list[str]) -> None:
    for p in created:
        out(f"  新しいタスクを作成しました: {p}")


def label_of(e: RawEvent) -> tuple[str, str]:
    if e.kind == "window":
        p = title_parser.parse(e.process, e.title)
        return p.app, p.label
    return {"idle": ("(離席)", ""), "stop": ("(停止)", "")}.get(e.kind, (e.kind, ""))


# =============================================================================
# タスクの記録
# =============================================================================


def cmd_start(app: App, args: argparse.Namespace) -> int:
    at = app.parse_time(args.at) if args.at else None
    before = app.tasks.current()
    a, created = app.tasks.switch(args.task, at=at, create=not args.no_create)
    if before and before.id == a.id and before.start_at == a.start_at:
        out(f"既に実行中です: {fmt_assignment(a, app.now())}")
        return 0
    print_created(created)
    if before and before.id != a.id:
        prev = app.storage.get_assignment(before.id)
        if prev:
            out(f"終了: {fmt_assignment(app.tasks.with_path(prev), app.now())}")
    out(f"開始: {a.task_path} (#{a.task_id})  {fmt_clock(a.start_at)}〜")
    return 0


def cmd_stop(app: App, args: argparse.Namespace) -> int:
    at = app.parse_time(args.at) if args.at else None
    a = app.tasks.stop(at)
    out(f"終了: {fmt_assignment(a, app.now())}")
    return 0


def cmd_resume(app: App, args: argparse.Namespace) -> int:
    at = app.parse_time(args.at) if args.at else None
    a, _ = app.tasks.resume(at)
    out(f"再開: {a.task_path} (#{a.task_id})  {fmt_clock(at or app.now())}〜")
    return 0


def _task_total_today(app: App, task_id: int) -> int:
    lo, hi = day_range(app.today(), app.settings.day_start_hour)
    now = app.now()
    return sum(
        max(0, min(a.end_at or now, hi) - max(a.start_at, lo))
        for a in app.tasks.list_assignments(lo, hi)
        if a.task_id == task_id
    )


def cmd_status(app: App, args: argparse.Namespace) -> int:
    now = app.now()
    cur = app.tasks.current()
    st = collector.status(app.storage, app.settings, now)
    today_ms = _task_total_today(app, cur.task_id) if cur else 0

    if args.json:
        window = None
        if st.last_event and st.last_event.kind == "window":
            appname, label = label_of(st.last_event)
            window = {**st.last_event.to_dict(), "app": appname, "label": label}
        emit_json({
            "now": now,
            "current": cur.to_dict() | {"elapsed_ms": cur.duration(now), "today_total_ms": today_ms}
            if cur else None,
            "collector": st.to_dict(),
            "window": window,
        })
        return 0

    if cur:
        out(f"現在のタスク: {cur.task_path} (#{cur.task_id})")
        out(f"  開始 {fmt_clock(cur.start_at)} / 経過 {fmt_duration(cur.duration(now), 'ja')}"
            f" / 今日の合計 {fmt_duration(today_ms, 'ja')}")
    else:
        out("現在のタスク: (なし)")
        last = app.storage.last_assignment()
        if last:
            last = app.tasks.with_path(last)
            out(f"  直前: {fmt_assignment(last, now)}  (resume で再開)")

    if st.running:
        out(f"ウィンドウ監視: 実行中 (PID {st.pid}, {fmt_clock(st.started_at)} から)")
    else:
        hb = f" 最終記録 {to_dt(st.heartbeat_at):%m/%d %H:%M}" if st.heartbeat_at else ""
        out(f"ウィンドウ監視: 停止中{hb}  (watch で開始)")
    e = st.last_event
    if st.running and e:
        if e.kind == "idle":
            out(f"  状態: 離席中 ({fmt_clock(e.started_at)}〜)")
        elif e.kind == "window":
            appname, label = label_of(e)
            out(f"  前面: [{appname}] {label}  ({fmt_clock(e.started_at, True)}〜)")
    return 0


def cmd_recent(app: App, args: argparse.Namespace) -> int:
    tasks = app.tasks.recent_tasks(args.n)
    if args.json:
        emit_json([{"key": f"@{i}", **t.to_dict()} for i, t in enumerate(tasks, 1)])
        return 0
    if not tasks:
        out("(まだ記録がありません)")
    for i, t in enumerate(tasks, 1):
        out(f"@{i}  {t.path}  (#{t.id})")
    return 0


def cmd_log(app: App, args: argparse.Namespace) -> int:
    d = app.parse_date(args.date)
    lo, hi = day_range(d, app.settings.day_start_hour)
    items = app.tasks.list_assignments(lo, hi)
    now = app.now()
    if args.json:
        emit_json([a.to_dict() | {"duration_ms": a.duration(now)} for a in items])
        return 0
    out(f"{fmt_date(d)} の割り当て")
    if not items:
        out("  (なし)")
        return 0
    dsh = app.settings.day_start_hour
    out(f"{'ID':>5}  {ljust('開始', 11)} {ljust('終了', 11)} {rjust('時間', 5)}  タスク")
    total = 0
    for a in items:
        end = fmt_clock_on(a.end_at, d, dsh) if a.end_at else "(進行中)"
        dur = a.duration(now)
        total += max(0, min(a.end_at or now, hi) - max(a.start_at, lo))
        src = " *" if a.source == "manual" else ""
        out(f"{a.id:>5}  {fmt_clock_on(a.start_at, d, dsh):<11} {ljust(end, 11)} {fmt_duration(dur):>5}  "
            f"{a.task_path} (#{a.task_id}){src}")
    out(f"合計 {fmt_duration(total, 'ja')}   (* = 手動で登録・修正したもの)")
    return 0


# =============================================================================
# 割り当ての修正
# =============================================================================


def _parse_event_range(text: str) -> tuple[int, int]:
    for sep in ("..", "-", ":"):
        if sep in text:
            a, b = text.split(sep, 1)
            break
    else:
        a = b = text
    try:
        return int(a), int(b)
    except ValueError:
        raise ActivityLogError(f"イベント範囲を解釈できません: {text!r} (例: 120..135)") from None


def cmd_assign_add(app: App, args: argparse.Namespace) -> int:
    if args.events:
        if args.from_ or args.to:
            raise ActivityLogError("--events と --from/--to は同時に指定できません")
        start, end = app.tasks.event_range(*_parse_event_range(args.events))
    else:
        if not (args.from_ and args.to):
            raise ActivityLogError("--from と --to (または --events) を指定してください")
        base = app.parse_date(args.date) if args.date else None
        start = app.parse_time(args.from_, base)
        end = app.parse_time(args.to, base)
    a, created = app.tasks.add_range(args.task, start, end, create=not args.no_create)
    print_created(created)
    out(f"登録: [{a.id}] {fmt_assignment(a, app.now())}")
    return 0


def cmd_assign_edit(app: App, args: argparse.Namespace) -> int:
    a = app.tasks.get_assignment(args.id)
    base = app.date_of(a.start_at)
    start = app.parse_time(args.start, base) if args.start else None
    end = app.parse_time(args.end, base) if args.end else None
    if start is None and end is None and args.task is None:
        raise ActivityLogError("--start / --end / --task のいずれかを指定してください")
    before = fmt_assignment(a, app.now())
    a, created = app.tasks.edit(args.id, start=start, end=end, task_spec=args.task,
                                create=not args.no_create)
    print_created(created)
    out(f"変更前: [{a.id}] {before}")
    out(f"変更後: [{a.id}] {fmt_assignment(a, app.now())}")
    return 0


def cmd_assign_delete(app: App, args: argparse.Namespace) -> int:
    for aid in args.ids:
        a = app.tasks.delete(aid)
        out(f"削除: [{a.id}] {fmt_assignment(a, app.now())}")
    return 0


# =============================================================================
# タスクの管理
# =============================================================================


def cmd_task_list(app: App, args: argparse.Namespace) -> int:
    tasks = app.tasks.list_tasks(include_archived=args.all)
    if args.json:
        emit_json([t.to_dict() for t in tasks])
        return 0
    if args.flat:
        for t in tasks:
            out(f"#{t.id:<4} {t.path}{'  [アーカイブ]' if t.archived else ''}")
        return 0
    ids = {t.id for t in tasks}
    children: dict[int | None, list[Task]] = {}
    for t in tasks:
        parent = t.parent_id if t.parent_id in ids else None
        children.setdefault(parent, []).append(t)

    def walk(parent: int | None, depth: int) -> None:
        for t in children.get(parent, []):
            mark = "  [アーカイブ]" if t.archived else ""
            out(f"{'  ' * depth}{t.name}  #{t.id}{mark}")
            walk(t.id, depth + 1)

    walk(None, 0)
    return 0


def cmd_task_add(app: App, args: argparse.Namespace) -> int:
    r = app.tasks.create_task(args.path)
    print_created(r.created)
    return 0


def cmd_task_rename(app: App, args: argparse.Namespace) -> int:
    t = app.tasks.rename_task(args.task, args.name)
    out(f"名前を変更しました: {t.path} (#{t.id})")
    return 0


def cmd_task_move(app: App, args: argparse.Namespace) -> int:
    t = app.tasks.move_task(args.task, args.parent)
    out(f"移動しました: {t.path} (#{t.id})")
    return 0


def cmd_task_archive(app: App, args: argparse.Namespace) -> int:
    archived = args.task_command == "archive"
    t = app.tasks.set_archived(args.task, archived)
    out(f"{'アーカイブしました' if archived else 'アーカイブを解除しました'}: {t.path} (#{t.id})")
    return 0


def cmd_task_merge(app: App, args: argparse.Namespace) -> int:
    src, dst, n = app.tasks.merge_task(args.src, args.dst)
    out(f"{src.path} (#{src.id}) の割り当て {n} 件を {dst.path} (#{dst.id}) に移し、"
        f"{src.path} をアーカイブしました")
    return 0


# =============================================================================
# ウィンドウ監視
# =============================================================================


def cmd_watch(app: App, args: argparse.Namespace) -> int:
    action = args.action
    if action == "status":
        return cmd_status(app, argparse.Namespace(json=args.json))
    if action == "stop":
        st = collector.status(app.storage, app.settings)
        if not st.running:
            out("ウィンドウ監視は実行されていません")
            return 0
        collector.request_stop(app.storage)
        out(f"停止を要求しました (PID {st.pid})。数秒以内に停止します")
        return 0
    return _watch_run(app, args)


def _watch_run(app: App, args: argparse.Namespace) -> int:
    if not winapi.IS_WINDOWS:
        raise ActivityLogError("ウィンドウ監視は Windows でのみ使えます")
    s = app.settings
    collector.setup_logging(s, verbose=args.verbose)
    lock = winapi.SingleInstanceLock(collector.LOCK_NAME, s.lock_port)
    if not lock.acquire():
        raise ActivityLogError("ウィンドウ監視は既に実行中です (watch status で確認できます)")

    def on_event(e: RawEvent) -> None:
        if args.quiet:
            return
        appname, label = label_of(e)
        out(f"{to_dt(e.started_at):%H:%M:%S}  {ljust(appname, 12)} {label}")

    try:
        c = collector.Collector(app.storage, app.tasks, s, collector.WinApiSource(), on_event=on_event)
        rec = c.start()
        if rec:
            out(f"前回の監視は {to_dt(rec.stop_at):%m/%d %H:%M:%S} に異常終了していたため、停止を記録しました")
            if rec.closed:
                out(f"  進行中だったタスクもこの時刻で終了しました: {rec.closed.task_path or ''}")
        out(f"ウィンドウ監視を開始しました (DB: {app.storage.path})。Ctrl+C か watch stop で終了します")
        try:
            collector.run_loop(c, s.poll_interval_ms)
        finally:
            c.shutdown()
            out("ウィンドウ監視を終了しました")
    finally:
        lock.release()
    return 0


def cmd_events(app: App, args: argparse.Namespace) -> int:
    now = app.now()
    d = app.parse_date(args.date)
    lo, hi = day_range(d, app.settings.day_start_hour)
    if args.from_:
        lo = app.parse_time(args.from_, d)
    if args.to:
        hi = app.parse_time(args.to, d)
    events = app.storage.raw_events_between(lo, hi)
    durations = {s.event.id: s.duration for s in timeline.raw_segments(app.storage, app.settings, lo, hi, now)}
    if args.kind:
        kinds = set(args.kind.split(","))
        events = [e for e in events if e.kind in kinds]
    if args.last:
        events = events[-args.last:]

    if args.json:
        rows = []
        for e in events:
            appname, label = label_of(e)
            rows.append(e.to_dict() | {"duration_ms": durations.get(e.id, 0), "app": appname, "label": label})
        emit_json(rows)
        return 0
    if not events:
        out("(記録なし)")
        return 0
    out(f"{'ID':>6}  {ljust('時刻', 8)} {rjust('時間', 5)}  {ljust('アプリ', 12)} 内容")
    for e in events:
        appname, label = label_of(e)
        if args.raw and e.kind == "window":
            label = f"{e.title}  <{e.process or '?'}>"
        dur = fmt_duration(durations[e.id]) if e.id in durations else ""
        out(f"{e.id:>6}  {fmt_clock(e.started_at, True):<8} {dur:>5}  {ljust(appname, 12)} {label}")
    return 0


# =============================================================================
# 出力
# =============================================================================


def _write_or_print(text: str, path: str | None) -> None:
    if path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        warn(f"書き出しました: {p}")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")


def cmd_report(app: App, args: argparse.Namespace) -> int:
    d = app.parse_date(args.date)
    summary = report.build_daily(app.storage, app.tasks, app.settings, d, app.now())
    if args.json:
        _write_or_print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), args.output)
        return 0
    text = report.render_markdown(summary, app.settings, top_windows=args.top,
                                  include_windows=not args.no_windows)
    _write_or_print(text, args.output)
    return 0


def cmd_export(app: App, args: argparse.Namespace) -> int:
    dsh = app.settings.day_start_hour
    start = end = None
    if args.date:
        start, end = day_range(app.parse_date(args.date), dsh)
    if args.from_:
        start = day_range(app.parse_date(args.from_), dsh)[0]
    if args.to:
        end = day_range(app.parse_date(args.to), dsh)[1]
    tables = tuple(args.tables.split(",")) if args.tables else export_mod.TABLES[:3]
    unknown = set(tables) - set(export_mod.TABLES)
    if unknown:
        raise ActivityLogError(f"不明なテーブル: {', '.join(sorted(unknown))} (選択肢: {', '.join(export_mod.TABLES)})")
    data = export_mod.export(app.storage, app.tasks, start=start, end=end, tables=tables, now=app.now())
    indent = None if args.compact else 2
    _write_or_print(json.dumps(data, ensure_ascii=False, indent=indent), args.output)
    return 0


# =============================================================================
# 設定
# =============================================================================


def cmd_config(app_or_none: App | None, args: argparse.Namespace) -> int:
    path = Path(args.config) if args.config else settings_mod.default_config_path()
    if args.config_command == "path":
        out(str(path))
        return 0
    if args.config_command == "init":
        if path.exists() and not args.force:
            raise ActivityLogError(f"設定ファイルは既にあります: {path} (--force で上書き)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(settings_mod.DEFAULT_TEMPLATE, encoding="utf-8")
        out(f"設定ファイルを作成しました: {path}")
        return 0
    s = settings_mod.load(path)
    if args.json:
        emit_json(s.to_dict() | {"config_path": str(path), "db_file": str(s.db_file), "log_file": str(s.log_file)})
        return 0
    out(f"# {path}{'' if path.exists() else ' (未作成・既定値)'}")
    out(f"# DB: {args.db or s.db_file}")
    out(f"# ログ: {s.log_file}")
    out(settings_mod.dumps(s.to_dict()).rstrip())
    return 0


# =============================================================================
# 引数の定義
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="worklog",
        description="行動ログツール: タスクの記録とアクティブウィンドウの収集",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", metavar="PATH", help="設定ファイル (既定: %%LOCALAPPDATA%%/ActivityLog/settings.toml)")
    p.add_argument("--db", metavar="PATH", help="DB ファイル (設定より優先)")
    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    json_opt = argparse.ArgumentParser(add_help=False)
    json_opt.add_argument("--json", action="store_true", help="JSON で出力する")

    # --- タスクの記録 ---
    s = sub.add_parser("start", aliases=["switch"], help="タスクを開始 (切替) する")
    s.add_argument("task", help=TASK_SPEC_HELP)
    s.add_argument("--at", metavar="TIME", help=f"開始時刻 (さかのぼり)。{TIME_HELP}")
    s.add_argument("--no-create", action="store_true", help="存在しないタスクを作らない")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("stop", help="進行中のタスクを終了する")
    s.add_argument("--at", metavar="TIME", help=f"終了時刻。{TIME_HELP}")
    s.set_defaults(func=cmd_stop)

    s = sub.add_parser("resume", help="直前のタスクを再開する")
    s.add_argument("--at", metavar="TIME", help=f"再開時刻。{TIME_HELP}")
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("status", aliases=["st"], parents=[json_opt], help="現在のタスクと監視状況を表示する")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("recent", parents=[json_opt], help="最近使ったタスク (@1, @2, ... で指定できる)")
    s.add_argument("-n", type=int, default=9, help="件数 (既定 9)")
    s.set_defaults(func=cmd_recent)

    s = sub.add_parser("log", aliases=["ls"], parents=[json_opt], help="1日分の割り当て一覧 (ID 付き)")
    s.add_argument("date", nargs="?", help="日付 (today, yesterday, -2, 2026-10-15 など。既定 today)")
    s.set_defaults(func=cmd_log)

    # --- 割り当ての修正 ---
    s = sub.add_parser("assign", help="割り当ての追加・修正・削除")
    asub = s.add_subparsers(dest="assign_command", metavar="ACTION", required=True)
    a = asub.add_parser("add", help="時間帯を指定してタスクに割り当てる (重なる割り当ては切り詰め)")
    a.add_argument("task", help=TASK_SPEC_HELP)
    a.add_argument("--from", dest="from_", metavar="TIME", help="開始時刻")
    a.add_argument("--to", metavar="TIME", help="終了時刻")
    a.add_argument("--date", help="--from/--to の日付 (既定 today)")
    a.add_argument("--events", metavar="A..B", help="生データのイベント ID の範囲で指定 (events コマンドで確認)")
    a.add_argument("--no-create", action="store_true", help="存在しないタスクを作らない")
    a.set_defaults(func=cmd_assign_add)
    a = asub.add_parser("edit", help="割り当ての開始・終了・タスクを変更する")
    a.add_argument("id", type=int, help="割り当て ID (log で確認)")
    a.add_argument("--start", metavar="TIME", help="新しい開始時刻 (日付省略時は元の日付)")
    a.add_argument("--end", metavar="TIME", help="新しい終了時刻")
    a.add_argument("--task", help=TASK_SPEC_HELP)
    a.add_argument("--no-create", action="store_true", help="存在しないタスクを作らない")
    a.set_defaults(func=cmd_assign_edit)
    a = asub.add_parser("delete", aliases=["rm"], help="割り当てを削除する")
    a.add_argument("ids", type=int, nargs="+", metavar="ID", help="割り当て ID")
    a.set_defaults(func=cmd_assign_delete)

    # --- タスクの管理 ---
    s = sub.add_parser("task", help="タスクの一覧・作成・名前変更・移動・アーカイブ")
    tsub = s.add_subparsers(dest="task_command", metavar="ACTION", required=True)
    t = tsub.add_parser("list", aliases=["ls"], parents=[json_opt], help="タスク一覧 (ツリー)")
    t.add_argument("--all", action="store_true", help="アーカイブ済みも表示する")
    t.add_argument("--flat", action="store_true", help="パスで1行ずつ表示する")
    t.set_defaults(func=cmd_task_list)
    t = tsub.add_parser("add", help="タスクを作成する (親/子 で階層ごと作成)")
    t.add_argument("path")
    t.set_defaults(func=cmd_task_add)
    t = tsub.add_parser("rename", help="名前を変更する")
    t.add_argument("task", help=TASK_SPEC_HELP)
    t.add_argument("name", help="新しい名前")
    t.set_defaults(func=cmd_task_rename)
    t = tsub.add_parser("move", help="親を付け替える (親を省略するとトップレベルへ)")
    t.add_argument("task", help=TASK_SPEC_HELP)
    t.add_argument("parent", nargs="?", help="新しい親タスク")
    t.set_defaults(func=cmd_task_move)
    t = tsub.add_parser("archive", help="アーカイブする (一覧・候補から隠す。記録は残る)")
    t.add_argument("task", help=TASK_SPEC_HELP)
    t.set_defaults(func=cmd_task_archive)
    t = tsub.add_parser("unarchive", help="アーカイブを解除する")
    t.add_argument("task", help=TASK_SPEC_HELP)
    t.set_defaults(func=cmd_task_archive)
    t = tsub.add_parser("merge", help="SRC の割り当てを DST に移して SRC をアーカイブする")
    t.add_argument("src")
    t.add_argument("dst")
    t.set_defaults(func=cmd_task_merge)

    # --- ウィンドウ監視 ---
    s = sub.add_parser("watch", parents=[json_opt], help="アクティブウィンドウの監視 (run / status / stop)")
    s.add_argument("action", nargs="?", default="run", choices=["run", "status", "stop"],
                   help="run: 監視を開始 (既定。このプロセスが常駐する) / status: 状況 / stop: 停止を要求")
    s.add_argument("-q", "--quiet", action="store_true", help="記録したウィンドウを表示しない")
    s.add_argument("-v", "--verbose", action="store_true", help="ログをコンソールにも出す")
    s.set_defaults(func=cmd_watch)

    s = sub.add_parser("events", parents=[json_opt], help="ウィンドウ・離席の生データ (タイトル履歴)")
    s.add_argument("date", nargs="?", help="日付 (既定 today)")
    s.add_argument("--from", dest="from_", metavar="TIME", help="この時刻以降")
    s.add_argument("--to", metavar="TIME", help="この時刻より前")
    s.add_argument("--kind", help="種別で絞り込み (window,idle,stop)")
    s.add_argument("-n", "--last", type=int, help="最後の N 件だけ表示する")
    s.add_argument("--raw", action="store_true", help="タイトルを切り出さずにそのまま表示する")
    s.set_defaults(func=cmd_events)

    # --- 出力 ---
    s = sub.add_parser("report", parents=[json_opt], help="日報 (Markdown) を作成する")
    s.add_argument("date", nargs="?", help="日付 (既定 today)")
    s.add_argument("-o", "--output", metavar="FILE", help="ファイルに書き出す (既定は標準出力)")
    s.add_argument("--top", type=int, default=10, help="よく使ったウィンドウの件数 (既定 10)")
    s.add_argument("--no-windows", action="store_true", help="よく使ったウィンドウを載せない")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("export", help="DB の内容を JSON で出力する")
    s.add_argument("--date", help="この日のデータだけ")
    s.add_argument("--from", dest="from_", metavar="DATE", help="この日以降")
    s.add_argument("--to", metavar="DATE", help="この日まで (この日を含む)")
    s.add_argument("--tables", help=f"出力するテーブル (既定 {','.join(export_mod.TABLES[:3])}。"
                                    f"選択肢: {','.join(export_mod.TABLES)})")
    s.add_argument("-o", "--output", metavar="FILE", help="ファイルに書き出す (既定は標準出力)")
    s.add_argument("--compact", action="store_true", help="改行・インデントなしで出力する")
    s.set_defaults(func=cmd_export)

    # --- 設定 ---
    s = sub.add_parser("config", parents=[json_opt], help="設定の表示・作成 (show / path / init)")
    s.add_argument("config_command", nargs="?", default="show", choices=["show", "path", "init"])
    s.add_argument("--force", action="store_true", help="init で既存の設定を上書きする")
    s.set_defaults(func=cmd_config, needs_db=False)

    return p


_TIME_OPTIONS = {"--at", "--from", "--to", "--start", "--end"}
_NEGATIVE_TIME = re.compile(r"-(?:\d+[hms])+")


def _join_negative_times(argv: list[str]) -> list[str]:
    """`--at -15m` を `--at=-15m` にする (argparse は '-' で始まる値をオプションとみなすため)。"""
    result: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] in _TIME_OPTIONS and i + 1 < len(argv) and _NEGATIVE_TIME.fullmatch(argv[i + 1]):
            result.append(f"{argv[i]}={argv[i + 1]}")
            i += 2
        else:
            result.append(argv[i])
            i += 1
    return result


def _setup_console() -> None:
    # Windows のコンソール (cp932) で表示できない文字があっても落ちないようにする
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    parser = build_parser()
    argv = _join_negative_times(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):  # コマンド省略時は status
        args = parser.parse_args([*argv, "status"])
    try:
        if getattr(args, "needs_db", True) is False:
            return args.func(None, args)
        app = App.open(Path(args.config) if args.config else None, args.db)
        try:
            return args.func(app, args)
        finally:
            app.close()
    except ActivityLogError as e:
        warn(f"エラー: {e}")
        return 1
    except KeyboardInterrupt:
        return 130
