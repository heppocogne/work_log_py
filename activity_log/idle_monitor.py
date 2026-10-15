"""無操作・スリープの推定。

- 無操作: 最後の入力からの経過時間が閾値を超えたら「離席」。最後の操作時刻にさかのぼって記録する
- 画面ロック: ロック中は入力がないので無操作として扱われる (区別しない)
- スリープ: ポーリング間隔が sleep_gap 以上空いたら、前回のポーリング時刻で停止していたとみなす
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IdleChange:
    became_idle: bool  # True: 離席になった / False: 復帰した
    at: int  # 離席の場合は最後の操作時刻


class IdleMonitor:
    def __init__(self, threshold_ms: int) -> None:
        self.threshold_ms = threshold_ms
        self.idle = False

    def update(self, now: int, idle_ms: int | None) -> IdleChange | None:
        if idle_ms is None:
            return None
        if not self.idle and idle_ms >= self.threshold_ms:
            self.idle = True
            return IdleChange(True, now - idle_ms)
        if self.idle and idle_ms < self.threshold_ms:
            self.idle = False
            return IdleChange(False, now)
        return None

    def reset(self) -> None:
        self.idle = False


class SleepDetector:
    def __init__(self, gap_ms: int) -> None:
        self.gap_ms = gap_ms
        self.last_tick: int | None = None

    def update(self, now: int) -> int | None:
        """スリープしていたと判断したら、停止したとみなす時刻 (前回のポーリング時刻) を返す。"""
        last, self.last_tick = self.last_tick, now
        if last is not None and now - last >= self.gap_ms:
            return last
        return None
