"""起動用スクリプト: python worklog.py <command> ...

pip install せずにリポジトリを置いただけで使えるようにするためのもの。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from activity_log.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
