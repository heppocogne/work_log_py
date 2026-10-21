"""CLI 版を単一ファイルの zipapp (worklog.pyz) にまとめる。

使い方: python scripts/build_pyz.py [-o dist/worklog.pyz]
標準ライブラリのみで動くので、Python 3.11 以上があれば `python worklog.pyz <command>` で実行できる。
"""

import argparse
import shutil
import sys
import tempfile
import zipapp
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def build(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        stage = Path(tmp)
        shutil.copytree(
            ROOT / "activity_log",
            stage / "activity_log",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        zipapp.create_archive(
            stage,
            output,
            interpreter="/usr/bin/env python3",
            main="activity_log.cli:main",
            compressed=True,
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=ROOT / "dist" / "worklog.pyz")
    args = parser.parse_args()
    print(build(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
