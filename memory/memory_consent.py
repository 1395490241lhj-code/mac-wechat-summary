"""The gate that decides whether a memory store may exist at all.

Writing normalised message text into a second local database is a second act of
local persistence. The project already has a consent for exactly that question
— *may extracted text be written down* — and it is owned by the macOS app, not
by this package. So this module does not invent a new consent. It looks for the
existing one and refuses when it cannot find it.

Three conditions, all required:

1. **The operator asked for a memory store in this process.**
   ``WECHAT_COMPANION_MEMORY_ENABLED=1``. Mirrors the bridge's
   ``WECHAT_COMPANION_ALLOW_AGENT_READ``: an explicit act, never a default.
2. **The operator named the file.** ``WECHAT_COMPANION_MEMORY_DB_PATH``. There
   is no default location, so no store is ever created in a place nobody chose,
   and the memory store is never the app's own store.
3. **The app's own consent state says yes.** The macOS app writes a small,
   versioned, generation-counted dictionary under ``consent.state`` in its
   preference domain every time the local-persistence consent is decided,
   including the explicit "no" of a fresh install. It is *read* here, never
   written, and never mirrored into a second copy that could drift.

Fail closed, and say which condition failed
-------------------------------------------

The app is the authority. Three refusals follow from that, and none of them
falls back to the operator's own assertion, because an operator asserting the
user's consent is not the user's consent:

* ``consent_state_missing`` — no state at all. A fresh install, or an app older
  than the state format. Denied: absence is "no", not "unknown".
* ``consent_state_malformed`` — a state exists but fails strict validation
  (unknown version, missing field, wrong type, negative generation). Denied
  wholesale; no field is picked out of a broken value.
* ``consent_withheld`` — a well-formed state that says no.

``consent_unobservable`` remains for the case where the preference domain
could not be read at all (the read itself failed). It is also a refusal.

Nothing here infers consent from the existence of a message database, from
the age of a file, or from an environment variable: the two variables above
are the operator's *activation* of this process, and activation is not
consent.

Withdrawal is honoured the same way the app honours it: a store that already
exists is not deleted when consent goes off (withdrawal is not a delete
request, [[Constraints]]), but nothing further may be written or read through
this package while it is off, because every entry point goes through this gate.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping

#: The operator's explicit request for a memory store in this process.
MEMORY_ENABLED_ENV: str = "WECHAT_COMPANION_MEMORY_ENABLED"

#: The explicit path of the memory database. No default exists.
MEMORY_DB_PATH_ENV: str = "WECHAT_COMPANION_MEMORY_DB_PATH"

#: The macOS app's preference domain and the key of its consent state. Both are
#: the app's, mirrored here as constants only so this module can read them;
#: nothing in this package ever writes to that domain.
APP_PREFERENCE_DOMAIN: str = "com.lianghongjing.WeChatCompanion"
CONSENT_STATE_KEY: str = "consent.state"

#: The one shape of state this reader understands. Must match
#: ``LocalPersistenceConsentState.schemaVersion`` in the app.
CONSENT_STATE_VERSION: int = 1

#: A reader returns the raw stored dictionary, ``None`` when there is none,
#: and raises when the domain could not be read at all.
ConsentStateReader = Callable[[], Mapping[str, Any] | None]


class MemoryConsentError(Exception):
    """Raised when something tries to use a store the gate refused.

    ``state`` is a fixed lowercase token and ``detail`` a fixed sentence.
    Neither carries chat content, a sender, or a filesystem path: both are safe
    to log and safe to return to a client.
    """

    def __init__(self, state: str, detail: str) -> None:
        super().__init__(detail)
        self.state = state
        self.detail = detail


@dataclass(frozen=True)
class ConsentState:
    """The app's consent state, after strict validation.

    Every field is required and typed; a value that does not validate is not
    partially trusted. Booleans are checked with ``is`` so a plist integer 1
    is not mistaken for a decision.
    """

    allows_local_message_storage: bool
    allows_memory_storage: bool
    generation: int
    updated_at: float

    @classmethod
    def parse(cls, raw: Mapping[str, Any]) -> "ConsentState | None":
        version = raw.get("version")
        if not _is_int(version) or version != CONSENT_STATE_VERSION:
            return None
        local = raw.get("allowsLocalMessageStorage")
        memory = raw.get("allowsMemoryStorage")
        generation = raw.get("generation")
        updated = raw.get("updatedAt")
        if local is not True and local is not False:
            return None
        if memory is not True and memory is not False:
            return None
        if not _is_int(generation) or generation < 0:
            return None
        if isinstance(updated, bool) or not isinstance(updated, (int, float)):
            return None
        return cls(
            allows_local_message_storage=local,
            allows_memory_storage=memory,
            generation=int(generation),
            updated_at=float(updated),
        )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class ConsentDecision:
    """The outcome of the gate.

    ``database_path`` is populated only when ``allowed`` is true, so a refusal
    cannot be turned into an open by reading a path off it.
    """

    allowed: bool
    state: str
    detail: str
    #: What was actually checked, in fixed tokens, for evidence documents.
    evidence: tuple[str, ...] = ()
    database_path: str | None = None
    #: The generation of the app state the decision was made against, when
    #: one was read. Lets evidence say "generation 7 said yes" without a clock.
    consent_generation: int | None = None

    def require(self) -> str:
        """The path, or the refusal as an exception. The only way in."""
        if not self.allowed or self.database_path is None:
            raise MemoryConsentError(self.state, self.detail)
        return self.database_path


def read_app_consent_state_macos() -> Mapping[str, Any] | None:
    """Reads the app's consent state from its preference domain.

    ``defaults export`` is asked first because ``cfprefsd`` may hold a value
    that has not reached the plist yet; the plist is read directly only when
    the tool is unavailable. A domain that does not exist yields ``None`` (no
    state -- a fresh install). A read that fails raises, which the gate
    reports as unobservable. Neither path ever guesses.
    """
    try:
        completed = subprocess.run(
            ["defaults", "export", APP_PREFERENCE_DOMAIN, "-"],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None
    if completed is not None and completed.returncode == 0 and completed.stdout.strip():
        contents = plistlib.loads(completed.stdout)
        return _state_from(contents)
    return _read_app_consent_state_plist()


def _read_app_consent_state_plist() -> Mapping[str, Any] | None:
    path = os.path.expanduser(
        f"~/Library/Preferences/{APP_PREFERENCE_DOMAIN}.plist"
    )
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        contents = plistlib.load(handle)
    return _state_from(contents)


def _state_from(contents: Any) -> Mapping[str, Any] | None:
    if not isinstance(contents, Mapping):
        return None
    value = contents.get(CONSENT_STATE_KEY)
    return value if isinstance(value, Mapping) else None


def resolve_consent(
    environment: Mapping[str, str] | None = None,
    read_app_consent_state: ConsentStateReader = read_app_consent_state_macos,
) -> ConsentDecision:
    """Evaluates all three conditions, in the order that leaks least.

    The operator's own two conditions are checked first, so a machine that was
    never asked for a memory store is never asked about the user's consent
    either. They are an *activation* gate; the third condition is the consent.
    """
    environment = os.environ if environment is None else environment
    if environment.get(MEMORY_ENABLED_ENV) != "1":
        return ConsentDecision(
            allowed=False,
            state="memory_disabled",
            detail=(
                "The local memory store is off. "
                f"Set {MEMORY_ENABLED_ENV}=1 to ask for it."
            ),
            evidence=("operator_opt_in:absent",),
        )
    path = environment.get(MEMORY_DB_PATH_ENV, "").strip()
    if not path:
        return ConsentDecision(
            allowed=False,
            state="memory_not_configured",
            detail=(
                "No memory database is configured. "
                f"Set {MEMORY_DB_PATH_ENV} to an explicit path."
            ),
            evidence=("operator_opt_in:present", "path:absent"),
        )
    checked = ("operator_opt_in:present", "path:present")
    try:
        raw = read_app_consent_state()
    except Exception:  # noqa: BLE001 - any reader failure is unobservable
        return ConsentDecision(
            allowed=False,
            state="consent_unobservable",
            detail=(
                "The app's consent state could not be read, so no message text "
                "may be written. This is a refusal, not a default."
            ),
            evidence=(*checked, "app_consent:unobservable"),
        )
    if raw is None:
        return ConsentDecision(
            allowed=False,
            state="consent_state_missing",
            detail=(
                "WeChat Companion has not recorded a local-persistence decision, "
                "so no message text may be written."
            ),
            evidence=(*checked, "app_consent:missing"),
        )
    state = ConsentState.parse(raw)
    if state is None:
        return ConsentDecision(
            allowed=False,
            state="consent_state_malformed",
            detail=(
                "The app's consent state is not in a form this process "
                "understands, so no message text may be written."
            ),
            evidence=(*checked, "app_consent:malformed"),
        )
    if not state.allows_memory_storage:
        return ConsentDecision(
            allowed=False,
            state="consent_withheld",
            detail=(
                "Local persistence is off in WeChat Companion, so no message "
                "text may be written down."
            ),
            evidence=(*checked, "app_consent:off"),
            consent_generation=state.generation,
        )
    return ConsentDecision(
        allowed=True,
        state="consented",
        detail="Local persistence is on and a memory store was explicitly requested.",
        evidence=(*checked, "app_consent:on"),
        database_path=path,
        consent_generation=state.generation,
    )
