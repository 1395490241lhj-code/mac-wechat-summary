"""Make ``memory/`` and the reader boundary in ``bridge/`` importable.

Same pattern the shadow and bridge suites use: flat modules on ``sys.path``,
no package installation step, and no copy of anything that lives elsewhere.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "memory"))
sys.path.insert(0, str(ROOT / "bridge"))


import pytest  # noqa: E402

import memory_consent as consent  # noqa: E402
from memory_store import MemoryStore  # noqa: E402
from message_source import (  # noqa: E402
    SOURCE_DATABASE,
    SOURCE_VISUAL,
    NormalizedConversation,
    NormalizedMessage,
)


def app_state(allowed=True, *, generation=1, version=1, **overrides):
    """The dictionary the macOS app writes under ``consent.state``.

    Field names and types mirror ``LocalPersistenceConsentState.dictionary``
    in the app, which is the only writer of the real thing.
    """
    state = {
        "version": version,
        "allowsLocalMessageStorage": allowed,
        "allowsMemoryStorage": allowed,
        "generation": generation,
        "updatedAt": 1_700_000_000.0,
    }
    state.update(overrides)
    return state


def granted(tmp_path, *, flag=True):
    """A consent decision produced by the real gate, never hand-built.

    The tests must not be able to open a store by a route the product does not
    have, so they go through ``resolve_consent`` with a stub reader standing in
    for the macOS app's preference domain.
    """
    return consent.resolve_consent(
        environment={
            consent.MEMORY_ENABLED_ENV: "1",
            consent.MEMORY_DB_PATH_ENV: str(tmp_path / "memory.sqlite"),
        },
        read_app_consent_state=lambda: app_state(flag),
    )


@pytest.fixture()
def store(tmp_path):
    with MemoryStore.open(granted(tmp_path)) as opened:
        yield opened


# --- Synthetic reader-shaped fixtures ---------------------------------------
#
# Both shapes are invented. Nothing here came from a real WeChat window, a real
# database, or a real person; the Chinese strings are written for this test
# file. The point of having two shapes is that the memory layer must not be
# able to tell them apart except where the difference is real.


def visual_message(
    identifier,
    conversation_id,
    text,
    *,
    sender="同事A",
    ownership="other",
    observed_at=1_700_000_000.0,
    sequence=None,
    kind="text",
    visible_time="昨天 14:30",
):
    """The shape the visual capture path produces.

    An integer store rowid, a display string WeChat drew on screen, and a
    confidence below 1 because a model read it off pixels.
    """
    return NormalizedMessage(
        id=identifier,
        conversation_id=conversation_id,
        sequence=identifier if sequence is None else sequence,
        sender=sender,
        ownership=ownership,
        visible_time=visible_time,
        text=text,
        kind=kind,
        confidence=0.92,
        first_observed_at=observed_at,
        source=SOURCE_VISUAL,
    )


def database_message(
    identifier,
    conversation_id,
    text,
    *,
    sender="同事A",
    ownership="other",
    created_at=1_700_000_000.0,
    sequence=None,
    kind="text",
):
    """The shape the external database reader adapter produces.

    A WeChat local id, no visible-time string (the reader never saw a screen),
    and confidence 1.0 because a decoded row is exact.
    """
    return NormalizedMessage(
        id=identifier,
        conversation_id=conversation_id,
        sequence=identifier if sequence is None else sequence,
        sender=sender,
        ownership=ownership,
        visible_time=None,
        text=text,
        kind=kind,
        confidence=1.0,
        first_observed_at=created_at,
        source=SOURCE_DATABASE,
    )


def conversation(identifier, title, *, source=SOURCE_VISUAL, first=None, last=None):
    return NormalizedConversation(
        id=identifier,
        title=title,
        first_seen_at=first,
        last_seen_at=last,
        source=source,
    )


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
