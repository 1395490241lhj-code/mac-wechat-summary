import sys
from pathlib import Path

# The package under test is imported as `wechatdb`, so the repository root has
# to be importable regardless of where pytest was invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
