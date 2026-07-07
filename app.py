import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from medium_ai_reader.cron import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
