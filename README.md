# work_log_py — 行動ログツール (CLI 版)

作業時間の使い方を後から見直すためのツールです。
「何を開いていたか (生データ)」を自動で記録し、「何をしていたか (タスク)」を人が付けます。
計画書は [docs/plan.md](docs/plan.md) を参照してください。今回は Phase 1 のうち CLI で使える部分を実装しています。

- Python 3.11 以上 (標準ライブラリのみ。pywin32 も不要)
- ウィンドウ監視 (`watch`) は Windows のみ。それ以外のコマンドはどの OS でも動きます

## 使い方

```
python worklog.py <コマンド> [オプション]
# または
python -m activity_log <コマンド> [オプション]
```

`pip install -e .` すると `worklog` コマンドとしても使えます。以下は `worklog` と書きます。

### pyz 版 (単一ファイル)

[Releases](https://github.com/heppocogne/work_log_py/releases) から `worklog.pyz` をダウンロードすると、リポジトリなしで `python worklog.pyz <コマンド>` で使えます。
自分でビルドする場合は `python scripts/build_pyz.py` (出力: `dist/worklog.pyz`)。
`v0.1.0` のようなタグ (`activity_log/__init__.py` の `__version__` と一致させる) を push すると GitHub Actions が自動で Release を作成し、`worklog.pyz` を添付します。

### よく使う流れ

```sh
worklog watch                         # 別のコンソールで常駐させる (Ctrl+C で終了)
worklog start "案件A/手順書作成"       # タスク開始 (存在しない階層はその場で作成)
worklog start 案件B --at 10:15        # 10:15 にさかのぼって切替
worklog start @2                      # 最近使ったタスクの 2 番目に切替
worklog stop --at -5m                 # 5分前に終了
worklog resume                        # 直前のタスクを再開
worklog                               # = worklog status (現在のタスク・監視状況)
worklog log                           # 今日の割り当て一覧 (ID 付き)
worklog report -o 日報.md             # 日報を Markdown で書き出し
```

### コマンド一覧

| コマンド | 内容 |
|---|---|
| `start TASK [--at TIME] [--no-create]` | タスクを開始 (切替)。`--at` 以降の既存の割り当ては切り詰める |
| `stop [--at TIME]` | 進行中のタスクを終了 |
| `resume [--at TIME]` | 直前のタスクを再開 |
| `undo [ID] [--list]` | 直前の操作を取り消す。`ID` を指定するとその操作の前まで戻す (`--list` で履歴、直近 20 件) |
| `status [--json]` | 現在のタスク・経過時間・今日の合計、ウィンドウ監視の状況・前面ウィンドウ |
| `recent [-n N] [--json]` | 最近使ったタスク (`@1`〜で指定できる) |
| `log [DATE] [--json]` | 1日分の割り当て一覧 |
| `assign add TASK --from TIME --to TIME [--date DATE]` | 時間帯を指定して割り当て (さかのぼり登録・空白の穴埋め) |
| `assign add TASK --events A..B` | `events` で見たイベント ID の範囲を割り当て |
| `assign edit ASSIGN [--start TIME] [--end TIME] [--task TASK]` | 割り当ての修正 |
| `assign delete ASSIGN...` | 割り当ての削除 |
| `task list [--all] [--flat] [--json]` | タスク一覧 (ツリー) |
| `task add PATH` / `rename TASK NAME` / `move TASK [PARENT]` | タスクの作成・名前変更・親の付け替え |
| `task archive TASK` / `unarchive TASK` | アーカイブ (削除はしない。過去の記録は残る) |
| `task merge SRC DST` | SRC の割り当てを DST に移して SRC をアーカイブ |
| `watch [run]` | アクティブウィンドウの監視を開始 (このプロセスが常駐) |
| `watch status` / `watch stop` | 監視状況の確認 / 別のコンソールから停止 |
| `events [DATE] [--from TIME] [--to TIME] [--kind K] [-n N] [--raw] [--json]` | タイトル履歴 (生データ) |
| `report [DATE] [-o FILE] [--top N] [--no-windows] [--json]` | 日報 (Markdown) |
| `export [--date D \| --from D --to D] [--tables T,...] [-o FILE] [--compact]` | DB の内容を JSON で出力 |
| `config [show\|path\|init] [--force]` | 設定の表示・設定ファイルの作成 |

共通オプション: `--config PATH` (設定ファイル)、`--db PATH` (DB ファイル)。

### タスクの指定 (TASK)

| 書き方 | 意味 |
|---|---|
| `案件A/手順書作成` | パス。`/` で階層を区切る。親タスクにも記録できる |
| `#12` | タスク ID (`task list` で確認) |
| `@1` | 最近使ったタスクの番号 (`recent` で確認。1 が最新) |

### 割り当ての指定 (ASSIGN)

`assign edit` / `assign delete` で使います。

| 書き方 | 意味 |
|---|---|
| `12` | 割り当て ID (`log` の ID 列で確認) |
| `#3` | タスク ID 3 の、今日の割り当て |
| `@1` | 最近使ったタスクの 1 番目の、今日の割り当て |

`#` / `@` で指定したタスクの今日の割り当てが 1 件に絞れない場合 (0 件・複数件) はエラーになります。その場合は ID を数字で指定してください。

### 時刻・日付の指定

- 時刻 (TIME): `now` / `9:30` / `9:30:15` / `-15m` / `-1h30m` / `15分前` / `1時間前` / `2026-10-15 9:30` / `25:10` (翌日 1:10)
  - 日付を省略して未来になる場合は前日とみなします (0:10 に `23:50` と入力した場合など)
  - `assign edit` では日付を省略すると、その割り当ての日付になります
- 日付 (DATE): `today` / `yesterday` / `-2` (2日前) / `2026-10-15` / `10/15`

## 割り当てのルール

- 割り当ては時間帯が重ならないように保ちます。新しい範囲を登録すると、重なる既存の割り当ては切り詰め・分割されます
  - 例: 9:00〜進行中の A がある状態で `assign add B --from 10:00 --to 10:30` → A 9:00〜10:00 / B 10:00〜10:30 / A 10:30〜進行中
- 「今のタスク」は終了時刻のない1件だけです
- 同じタスクで隙間なく接する割り当ては1つにまとめます
- 手動で登録・修正した割り当ては `log` で `*` が付きます

## 取り消し (undo)

`start` / `stop` / `resume` / `assign add|edit|delete` / `task add|rename|move|archive|merge` は、実行前のタスクと割り当てを保存します。`worklog undo` で直前の操作を取り消せます。

- 取り消せるのは手動の操作だけです。ウィンドウ監視の記録 (`events`) と、監視による自動の停止補正は対象外です
- 変化のなかった操作 (実行中のタスクへの `start` など) や失敗した操作は履歴に残りません
- 履歴は直近 20 件です。やり直し (redo) はありません

## ウィンドウ監視

`watch` を実行したコンソールが常駐して、1秒おきに前面ウィンドウを確認し、変化したときだけ記録します。

- 無操作が 5分 (設定可) 続くと「離席」を最後の操作時刻にさかのぼって記録し、操作再開時に前面ウィンドウを記録し直します
- ポーリングの間隔が 30秒以上空いたらスリープしていたとみなし、停止を記録します
- 1分おきに生存時刻を記録し、異常終了していた場合は次回起動時に停止を補い、進行中のタスクも同じ時刻で閉じます
- 二重起動は名前付きミューテックス (使えなければ localhost のポート) で防ぎます
- 監視中でも別のコンソールから `start` / `stop` などは普通に使えます (SQLite WAL モード)
- エラーはログファイル (`%LOCALAPPDATA%/ActivityLog/app.log`) に記録されます

## 設定

`worklog config init` で `%LOCALAPPDATA%/ActivityLog/settings.toml` を作成します (場所は環境変数 `ACTIVITY_LOG_CONFIG` でも変更可)。

```toml
poll_interval_ms = 1000
idle_check_interval_ms = 5000
idle_threshold_minutes = 5
sleep_gap_seconds = 30
heartbeat_interval_seconds = 60
day_start_hour = 0                 # 4 にすると 0〜3 時台の作業は前日の日報に入る
exclude_processes = []             # 記録しないプロセス名
db_path = "%LOCALAPPDATA%/ActivityLog/activity.db"
log_path = "%LOCALAPPDATA%/ActivityLog/app.log"

[overlay]                          # GUI 版で使用予定
alpha = 0.7
```

## 構成 (GUI 版を見据えた分割)

```
activity_log/
├─ app.py           App: 設定・DB・サービスをまとめる入口 (CLI も GUI もここから使う)
├─ settings.py      設定 (TOML) の読み書き
├─ storage.py       SQLite アクセス、スキーマ管理 (PRAGMA user_version でマイグレーション)
├─ task_service.py  タスク作成・切替・割り当ての整合性維持 (重なりの切り詰めはここだけ)
├─ collector.py     前面ウィンドウの収集 (tick() を呼ぶだけで動く。スケジューラ非依存)
├─ idle_monitor.py  無操作・スリープの推定
├─ winapi.py        Windows API 呼び出しの集約 (ctypes)
├─ title_parser.py  タイトルからファイル名・接続先などを切り出す (表示用の正規表現ルール)
├─ timeline.py      生データの区間化と区間演算
├─ report.py        1日分の集計 (DailySummary) と Markdown 化
├─ export.py        JSON 出力
├─ timeutil.py      時刻の変換・入力の解釈
└─ cli.py           CLI (コア層を呼んで表示するだけ)
```

GUI 版 (tkinter) では次のように同じコア層を使う想定です。

| GUI の部品 | 使うもの |
|---|---|
| 常駐・収集 | `Collector.tick()` を `root.after()` から呼ぶ (CLI の `run_loop` の代わり)。`on_event` で表示を更新 |
| 常時表示 | `TaskService.current()`、`collector.status()` |
| タスク切替の小窓 | `TaskService.recent_tasks()` (1〜9 キー)、`list_tasks()` (検索)、`switch(task, at=...)` |
| 簡易確認画面 | `report.build_daily()` (割り当て一覧・タスク別合計・未割当)、`timeline.raw_segments()` (タイトル履歴)、`TaskService.edit()/delete()/add_range()`、`event_range()` (複数行選択→割り当て) |
| 設定の保存 | `settings.save()` (常時表示の位置など) |

コア層はエラーを `ActivityLogError` (利用者向けメッセージ) で返すので、GUI ではそのままダイアログに出せます。
CLI の一覧系コマンドは `--json` で同じデータを出力できます。

## 計画書からの変更点

- Windows API は pywin32 を使わず ctypes のみで実装しました (使う API が少ないため。外部パッケージ不要)
- 設定のキーは TOML の慣習に合わせて snake_case にしました
- 追加した機能: `resume`、`recent` / `@N` 指定、`assign add --events`、`task merge`、`day_start_hour` (日付の区切り)、日報の未割当時間・よく使ったウィンドウ、各コマンドの `--json`

## 開発

```sh
python -m unittest discover -s tests -t .
```
