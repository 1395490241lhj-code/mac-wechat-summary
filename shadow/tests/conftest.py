"""Make ``shadow/`` importable the way the CLI entry point does."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
