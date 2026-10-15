"""ウィンドウタイトルからファイル名・接続先などを切り出す (表示・集計用)。

生データには生のタイトルを保存し、切り出しは表示時に行う。
ルールを後から直しても過去データに適用できる。実際のタイトル形式は収集データを見て調整する。
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ParsedTitle:
    app: str  # 表示用のアプリ名
    label: str  # 表示用の主な内容 (ファイル名・接続先など)
    file: str | None = None
    workspace: str | None = None
    host: str | None = None


@dataclass
class Rule:
    processes: tuple[str, ...]  # 小文字で比較
    app: str
    pattern: re.Pattern[str]
    # パターンの名前付きグループ file / workspace / host を使う


def _rx(s: str) -> re.Pattern[str]:
    return re.compile(s)


_OFFICE_TAIL = r"(?:\s*[\[(][^\])]*[\])])*(?:\s+-\s+(?:読み取り専用|Read-Only|互換モード|Compatibility Mode))*"

RULES: list[Rule] = [
    Rule(
        ("powerpnt.exe",),
        "PowerPoint",
        _rx(r"^(?P<file>.+?)" + _OFFICE_TAIL + r"\s+-\s+(?:Microsoft\s+)?PowerPoint.*$"),
    ),
    Rule(
        ("excel.exe",),
        "Excel",
        _rx(r"^(?P<file>.+?)" + _OFFICE_TAIL + r"\s+-\s+(?:Microsoft\s+)?Excel.*$"),
    ),
    Rule(
        ("winword.exe",),
        "Word",
        _rx(r"^(?P<file>.+?)" + _OFFICE_TAIL + r"\s+-\s+(?:Microsoft\s+)?Word.*$"),
    ),
    # VS Code: "● memo.md - workspace - Visual Studio Code" / "workspace - Visual Studio Code"
    Rule(
        ("code.exe",),
        "VS Code",
        _rx(r"^(?:[●•]\s*)?(?:(?P<file>.+?)\s+-\s+)?(?P<workspace>[^-]+?)"
            r"(?:\s+\[[^\]]*\])?\s+-\s+Visual Studio Code.*$"),
    ),
    # リモートデスクトップ: "server01 - リモート デスクトップ接続"
    Rule(
        ("mstsc.exe",),
        "リモート",
        _rx(r"^(?P<host>.+?)\s+-\s+(?:リモート\s*デスクトップ接続|Remote Desktop Connection)$"),
    ),
]

_APP_NAMES = {
    "explorer.exe": "エクスプローラー",
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "outlook.exe": "Outlook",
    "teams.exe": "Teams",
    "ms-teams.exe": "Teams",
}


def app_name(process: str | None) -> str:
    if not process:
        return "(不明)"
    p = process.lower()
    for rule in RULES:
        if p in rule.processes:
            return rule.app
    return _APP_NAMES.get(p, re.sub(r"\.exe$", "", process, flags=re.I))


def parse(process: str | None, title: str | None) -> ParsedTitle:
    title = (title or "").strip()
    p = (process or "").lower()
    for rule in RULES:
        if p not in rule.processes:
            continue
        m = rule.pattern.match(title)
        if not m:
            break
        g = m.groupdict()
        file, ws, host = g.get("file"), g.get("workspace"), g.get("host")
        if file and ws:
            label = f"{file} ({ws})"
        else:
            label = file or ws or host or title
        return ParsedTitle(rule.app, label, file=file, workspace=ws, host=host)
    return ParsedTitle(app_name(process), title)
