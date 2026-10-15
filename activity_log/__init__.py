"""行動ログツール (Activity Log)。

コア層 (storage / task_service / collector / report / export) は UI 非依存。
CLI (cli.py) と将来の GUI (tkinter) はどちらもこのコア層を呼び出すだけの薄い層にする。
"""

__version__ = "0.1.0"
