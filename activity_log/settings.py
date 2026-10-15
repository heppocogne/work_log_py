"""設定 (TOML) の読み書き。

設定ファイルの場所: 環境変数 ACTIVITY_LOG_CONFIG > %LOCALAPPDATA%/ActivityLog/settings.toml
(Windows 以外では ~/.local/share/ActivityLog/settings.toml)
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 以前
    tomllib = None  # type: ignore[assignment]

from .errors import ActivityLogError

APP_DIR_NAME = "ActivityLog"


def _local_appdata() -> str:
    if v := os.environ.get("LOCALAPPDATA"):
        return v
    return str(Path.home() / ".local" / "share")


def expand_path(value: str) -> Path:
    """%VAR% / $VAR / ~ を展開する (%LOCALAPPDATA% は Windows 以外でも使えるようにする)。"""

    def repl(m: re.Match[str]) -> str:
        name = m[1]
        if name.upper() == "LOCALAPPDATA":
            return _local_appdata()
        return os.environ.get(name, m[0])

    value = re.sub(r"%([^%]+)%", repl, value)
    return Path(os.path.expanduser(os.path.expandvars(value)))


def default_config_path() -> Path:
    if v := os.environ.get("ACTIVITY_LOG_CONFIG"):
        return expand_path(v)
    return Path(_local_appdata()) / APP_DIR_NAME / "settings.toml"


@dataclass
class OverlaySettings:
    """GUI (常時表示) 用。CLI では使わないが、設定ファイルの形はここで決めておく。"""

    x: int | None = None
    y: int | None = None
    alpha: float = 0.7
    collapsed: bool = False
    topmost_refresh_seconds: int = 5


@dataclass
class Settings:
    poll_interval_ms: int = 1000
    idle_check_interval_ms: int = 5000
    idle_threshold_minutes: float = 5
    sleep_gap_seconds: int = 30
    heartbeat_interval_seconds: int = 60
    # 論理日付の区切り (例: 4 にすると 0:00〜3:59 の作業は前日の日報に入る)
    day_start_hour: int = 0
    # 記録しないプロセス名 (大文字小文字は区別しない)。例: ["LockApp.exe"]
    exclude_processes: list[str] = field(default_factory=list)
    # 二重起動防止 (ミューテックスが使えない環境で bind するポート)
    lock_port: int = 47651
    db_path: str = "%LOCALAPPDATA%/ActivityLog/activity.db"
    log_path: str = "%LOCALAPPDATA%/ActivityLog/app.log"
    overlay: OverlaySettings = field(default_factory=OverlaySettings)

    # 読み込み元 (保存先)。設定項目ではない
    source_path: Path | None = field(default=None, repr=False, compare=False)

    @property
    def db_file(self) -> Path:
        return expand_path(self.db_path)

    @property
    def log_file(self) -> Path:
        return expand_path(self.log_path)

    @property
    def idle_threshold_ms(self) -> int:
        return int(self.idle_threshold_minutes * 60_000)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for f in fields(self):
            if f.name == "source_path":
                continue
            v = getattr(self, f.name)
            if isinstance(v, OverlaySettings):
                v = {k.name: getattr(v, k.name) for k in fields(v)}
            d[f.name] = v
        return d


def load(path: Path | None = None) -> Settings:
    """設定を読み込む。ファイルがなければ既定値。未知のキーは警告して無視する。"""
    path = path or default_config_path()
    settings = Settings(source_path=path)
    if not path.exists():
        return settings
    if tomllib is None:
        raise ActivityLogError("設定ファイルの読み込みには Python 3.11 以降が必要です")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ActivityLogError(f"設定ファイルを読み込めません: {path}: {e}") from e
    _apply(settings, data, "")
    return settings


def _apply(target: Any, data: dict[str, Any], prefix: str) -> None:
    names = {f.name: f for f in fields(target) if f.name != "source_path"}
    for key, value in data.items():
        if key not in names:
            print(f"警告: 未知の設定項目を無視します: {prefix}{key}", file=sys.stderr)
            continue
        current = getattr(target, key)
        if isinstance(current, OverlaySettings):
            if isinstance(value, dict):
                _apply(current, value, f"{key}.")
            continue
        setattr(target, key, value)


def save(settings: Settings, path: Path | None = None) -> Path:
    """設定を書き出す (GUI で常時表示の位置を保存するときなどに使う)。"""
    path = path or settings.source_path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(settings.to_dict()), encoding="utf-8")
    return path


def dumps(data: dict[str, Any]) -> str:
    """フラットな値と1段のテーブルだけを扱う最小限の TOML 書き出し。None は書かない。"""
    lines: list[str] = []
    tables: list[tuple[str, dict[str, Any]]] = []
    for k, v in data.items():
        if isinstance(v, dict):
            tables.append((k, v))
        elif v is not None:
            lines.append(f"{k} = {_toml_value(v)}")
    for name, table in tables:
        lines.append("")
        lines.append(f"[{name}]")
        for k, v in table.items():
            if v is not None:
                lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"TOML に書き出せない値です: {v!r}")


DEFAULT_TEMPLATE = """\
# 行動ログツールの設定

poll_interval_ms = 1000            # 前面ウィンドウの確認間隔
idle_check_interval_ms = 5000      # 無操作の確認間隔
idle_threshold_minutes = 5         # これ以上操作がなければ離席とみなす
sleep_gap_seconds = 30             # 確認間隔がこれ以上空いたらスリープしていたとみなす
heartbeat_interval_seconds = 60    # 生存時刻の記録間隔 (異常終了時の補正に使う)
day_start_hour = 0                 # 日付の区切り (4 にすると 0〜3 時台は前日扱い)
exclude_processes = []             # 記録しないプロセス名 例: ["LockApp.exe"]
lock_port = 47651                  # 二重起動防止に使うポート (ミューテックスが使えない場合)
db_path = "%LOCALAPPDATA%/ActivityLog/activity.db"
log_path = "%LOCALAPPDATA%/ActivityLog/app.log"

[overlay]                          # GUI 版の常時表示
alpha = 0.7
collapsed = false
topmost_refresh_seconds = 5
# x = 100
# y = 100
"""
