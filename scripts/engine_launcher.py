"""从任意当前目录启动skill内置引擎。"""

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cashflow_main.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
