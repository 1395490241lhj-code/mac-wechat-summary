import sys
from pathlib import Path

# The same flat, explicit cross-tree import style the repository already uses:
# the package under test is imported as `wechatdb`, and the provider will build
# results against the Reader boundary in `bridge/`. Both go on sys.path here,
# and nothing is installed, discovered or resolved from the environment.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bridge"))
