from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

from sjtuflow.cli.main import main


if __name__ == "__main__":
    raise SystemExit(main())
