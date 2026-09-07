"""Make ``shadow/`` importable the way the CLI entry point does."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# --- Real-user-data guard (M2.2e) -------------------------------------------
#
# M2.2d shipped a bug in which a test host resolved the *real* app-owned store
# and synced the user's own messages into it. Nothing about that was specific
# to one test, so the guard is not either: every test in this suite runs with
# an isolated ``HOME``, so code that derives the canonical location
# (``Path.home()`` / ``$HOME``) reaches a temporary directory and cannot find
# the user's. A test that genuinely needs the real one marks itself
# ``@pytest.mark.real_data`` and says so out loud.
#
# The captured constant is taken at import, *before* any redirection, so the
# session check below is about the real path even while the tests are not.

REAL_APP_SUPPORT = Path.home() / "Library" / "Application Support" / "WeChatCompanion"
REAL_MEMORY_STORE = REAL_APP_SUPPORT / "memory.sqlite"


@pytest.fixture(autouse=True)
def isolated_home(request, tmp_path_factory, monkeypatch):
    """Every test gets a home of its own unless it asks for the real one."""
    if request.node.get_closest_marker("real_data"):
        yield
        return
    monkeypatch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
    yield


@pytest.fixture(autouse=True, scope="session")
def the_real_memory_store_is_never_touched():
    """Detection behind the prevention: the real store must not appear."""
    existed = REAL_MEMORY_STORE.exists()
    before = REAL_MEMORY_STORE.stat().st_mtime if existed else None
    yield
    now = REAL_MEMORY_STORE.exists()
    assert now == existed, (
        "the suite created or removed the user's real memory store; "
        "a test resolved the canonical location instead of an isolated one"
    )
    if existed:
        assert REAL_MEMORY_STORE.stat().st_mtime == before, (
            "the suite wrote to the user's real memory store"
        )
