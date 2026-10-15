"""前面ウィンドウの収集。

Collector はスケジューラに依存しない: tick() を定期的に呼ぶだけで動く。
- CLI: run_loop() で time.sleep しながら呼ぶ
- GUI: root.after(poll_interval_ms, ...) から呼ぶ (すべてメインスレッドで動かす方針)

監視の状態は app_state に書くので、別プロセス (CLI の status / watch stop) からも確認・停止できる。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from . import winapi
from .idle_monitor import IdleMonitor, SleepDetector
from .models import Assignment, CollectorStatus, RawEvent
from .settings import Settings
from .storage import Storage
from .task_service import TaskService
from .timeutil import now_ms

log = logging.getLogger(__name__)

KEY_PID = "collector.pid"
KEY_STARTED = "collector.started_at"
KEY_HEARTBEAT = "collector.heartbeat"
KEY_STOP_REQUEST = "collector.stop_request"

LOCK_NAME = "ActivityLog.Collector"


class WindowSource(Protocol):
    def foreground(self) -> winapi.WindowInfo | None: ...
    def idle_ms(self) -> int | None: ...


class WinApiSource:
    def foreground(self) -> winapi.WindowInfo | None:
        return winapi.foreground_window()

    def idle_ms(self) -> int | None:
        return winapi.idle_ms()


@dataclass
class Recovery:
    """前回の異常終了の補正内容。"""

    stop_at: int
    closed: Assignment | None


class Collector:
    def __init__(
        self,
        storage: Storage,
        tasks: TaskService,
        settings: Settings,
        source: WindowSource,
        *,
        clock: Callable[[], int] = now_ms,
        pid: int | None = None,
        on_event: Callable[[RawEvent], None] | None = None,
    ) -> None:
        self.db = storage
        self.tasks = tasks
        self.settings = settings
        self.source = source
        self.clock = clock
        self.pid = os.getpid() if pid is None else pid
        self.on_event = on_event
        self.exclude = {p.lower() for p in settings.exclude_processes}
        self.idle = IdleMonitor(settings.idle_threshold_ms)
        self.sleep = SleepDetector(settings.sleep_gap_seconds * 1000)
        self.started_at: int | None = None
        self._last_key: tuple[str | None, str] | None = None
        self._last_event_at = 0
        self._last_idle_check: int | None = None
        self._last_heartbeat: int | None = None
        self._stopped = True  # 直近の記録が stop (= 次の window は必ず記録する)

    # --- ライフサイクル -------------------------------------------------------

    def start(self) -> Recovery | None:
        recovery = self.recover()
        now = self.clock()
        self.started_at = now
        self.db.set_state(KEY_PID, self.pid)
        self.db.set_state(KEY_STARTED, now)
        self.db.set_state(KEY_HEARTBEAT, now)
        self._last_heartbeat = now
        last = self.db.last_raw_event()
        self._last_event_at = last.started_at if last else 0
        log.info("collector started (pid=%s)", self.pid)
        return recovery

    def recover(self) -> Recovery | None:
        """最後の生データが stop でなければ、最後の生存時刻で stop を補い、進行中の割り当ても閉じる。"""
        last = self.db.last_raw_event()
        if last is None or last.kind == "stop":
            return None
        hb = self.db.get_state_int(KEY_HEARTBEAT) or last.started_at
        at = max(hb, last.started_at)
        self._add(at, "stop")
        closed = self.tasks.close_open_before(at)
        log.warning("recovered from abnormal termination: stop at %s", at)
        return Recovery(at, closed)

    def shutdown(self) -> None:
        now = self.clock()
        if not self._stopped:
            self._add(max(now, self._last_event_at), "stop")
        if self.db.get_state(KEY_PID) == str(self.pid):
            self.db.delete_state(KEY_PID)
        self.db.set_state(KEY_HEARTBEAT, now)
        log.info("collector stopped")

    def stop_requested(self) -> bool:
        req = self.db.get_state_int(KEY_STOP_REQUEST)
        return req is not None and self.started_at is not None and req >= self.started_at

    # --- 定期処理 -------------------------------------------------------------

    def tick(self) -> None:
        now = self.clock()

        slept_at = self.sleep.update(now)
        if slept_at is not None:
            log.info("sleep detected: %s -> %s", slept_at, now)
            if not self._stopped:
                self._add(max(slept_at, self._last_event_at), "stop")
            self.idle.reset()
            self._last_idle_check = None  # 復帰直後に無操作を確認し直す

        if self._last_idle_check is None or now - self._last_idle_check >= self.settings.idle_check_interval_ms:
            self._last_idle_check = now
            change = self.idle.update(now, self.source.idle_ms())
            if change is not None:
                if change.became_idle:
                    # 最後の操作時刻にさかのぼる (ただし直前の記録より前にはしない)
                    at = now if self._stopped else max(change.at, self._last_event_at)
                    self._add(at, "idle")
                else:
                    self._last_key = None  # 復帰したら前面ウィンドウを必ず記録する

        if not self.idle.idle:
            self._poll_window(now)

        hb_interval = self.settings.heartbeat_interval_seconds * 1000
        if self._last_heartbeat is None or now - self._last_heartbeat >= hb_interval:
            self.db.set_state(KEY_HEARTBEAT, now)
            self._last_heartbeat = now

    def _poll_window(self, now: int) -> None:
        w = self.source.foreground()
        if w is None or not w.title.strip():
            return
        if w.pid == self.pid:
            return  # 自分自身 (GUI の常時表示・小窓)
        if w.process and w.process.lower() in self.exclude:
            return
        key = (w.process, w.title)
        if key == self._last_key and not self._stopped:
            return
        self._last_key = key
        self._add(now, "window", w.process, w.title)

    def _add(self, at: int, kind: str, process: str | None = None, title: str | None = None) -> None:
        ev = self.db.add_raw_event(at, kind, process, title)
        self._last_event_at = at
        self._stopped = kind == "stop"
        if kind != "window":
            self._last_key = None
        if self.on_event:
            self.on_event(ev)


def run_loop(collector: Collector, poll_interval_ms: int,
             sleep: Callable[[float], None] = time.sleep) -> None:
    """CLI 用の常駐ループ。Ctrl+C か watch stop で終了する。定期処理の例外ではループを止めない。"""
    try:
        while True:
            try:
                collector.tick()
                if collector.stop_requested():
                    log.info("stop requested")
                    break
            except Exception:
                log.exception("error in collector tick")
            sleep(poll_interval_ms / 1000)
    except KeyboardInterrupt:
        pass


def status(storage: Storage, settings: Settings, now: int | None = None) -> CollectorStatus:
    """監視プロセスの状態 (別プロセスから見たもの)。生存時刻が新しければ実行中とみなす。"""
    now = now_ms() if now is None else now
    pid = storage.get_state_int(KEY_PID)
    hb = storage.get_state_int(KEY_HEARTBEAT)
    limit = settings.heartbeat_interval_seconds * 2000 + 10_000
    running = pid is not None and hb is not None and now - hb <= limit
    last = storage.last_raw_event()
    return CollectorStatus(
        running=running,
        pid=pid if running else None,
        started_at=storage.get_state_int(KEY_STARTED) if running else None,
        heartbeat_at=hb,
        last_event=last,
        idle=running and last is not None and last.kind == "idle",
    )


def request_stop(storage: Storage, now: int | None = None) -> None:
    storage.set_state(KEY_STOP_REQUEST, now_ms() if now is None else now)


def setup_logging(settings: Settings, verbose: bool = False) -> None:
    """例外などをログファイルに記録する (pythonw ではコンソールが出ないため)。"""
    path = settings.log_file
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.FileHandler(path, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
