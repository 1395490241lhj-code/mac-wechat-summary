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
3. **The user's local-persistence consent is observed to be on.** This is the
   authoritative flag, and it lives in the app's own preferences under
   ``persistence.allowsLocalMessageStorage``. It is *read*, never written, and
   never mirrored into a second copy that could drift.

Fail closed, and say which condition failed
-------------------------------------------

If the app's flag cannot be observed at all — the app has never run, the
preference domain does not exist, the platform is not the one the app runs on,
the read failed — this module returns a refusal with the state
``consent_unobservable``. It does **not** fall back to the operator's own
assertion, because an operator asserting the user's consent is not the user's
consent. That refusal is the documented gap: a Python process has no first-class
channel to the app's consent, only its preferences, so a memory store cannot be
opened on a machine where the app has never stored anything. Fixing that
properly is app work, not something this package may paper over.

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
from typing import Callable, Mapping

#: The operator's explicit request for a memory store in this process.
MEMORY_ENABLED_ENV: str = "WECHAT_COMPANION_MEMORY_ENABLED"

#: The explicit path of the memory database. No default exists.
MEMORY_DB_PATH_ENV: str = "WECHAT_COMPANION_MEMORY_DB_PATH"

#: The macOS app's preference domain and the key inside it. Both are the app's,
#: mirrored here as constants only so this module can read them; nothing in
#: this package ever writes to that domain.
APP_PREFERENCE_DOMAIN: str = "com.lianghongjing.WeChatCompanion"
LOCAL_PERSISTENCE_CONSENT_KEY: str = "persistence.allowsLocalMessageStorage"

#: A reader answers ``True``, ``False``, or ``None`` for "not observable".
ConsentFlagReader = Callable[[], bool | None]


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

    def require(self) -> str:
        """The path, or the refusal as an exception. The only way in."""
        if not self.allowed or self.database_path is None:
            raise MemoryConsentError(self.state, self.detail)
        return self.database_path


def read_app_consent_flag_macos() -> bool | None:
    """Reads the app's local-persistence flag from its preference domain.

    ``defaults`` is asked first because ``cfprefsd`` may be holding a value
    that has not reached the plist yet, and the plist is read only if the tool
    is unavailable. Any failure returns ``None`` — "not observable" — never a
    guess in either direction.
    """
    try:
        completed = subprocess.run(
            ["defaults", "read", APP_PREFERENCE_DOMAIN, LOCAL_PERSISTENCE_CONSENT_KEY],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        completed = None
    if completed is not None and completed.returncode == 0:
        value = completed.stdout.strip()
        if value in {"1", "true", "YES"}:
            return True
        if value in {"0", "false", "NO"}:
            return False
        return None
    return _read_app_consent_flag_plist()


def _read_app_consent_flag_plist() -> bool | None:
    path = os.path.expanduser(
        f"~/Library/Preferences/{APP_PREFERENCE_DOMAIN}.plist"
    )
    try:
        with open(path, "rb") as handle:
            contents = plistlib.load(handle)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    value = contents.get(LOCAL_PERSISTENCE_CONSENT_KEY)
    return value if isinstance(value, bool) else None


def resolve_consent(
    environment: Mapping[str, str] | None = None,
    read_app_consent_flag: ConsentFlagReader = read_app_consent_flag_macos,
) -> ConsentDecision:
    """Evaluates all three conditions, in the order that leaks least.

    The operator's own two conditions are checked first, so a machine that was
    never asked for a memory store is never asked about the user's consent
    either.
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
    try:
        flag = read_app_consent_flag()
    except Exception:  # noqa: BLE001 - any reader failure is unobservable
        flag = None
    if flag is None:
        return ConsentDecision(
            allowed=False,
            state="consent_unobservable",
            detail=(
                "The local-persistence consent could not be observed, so no "
                "message text may be written. This is a refusal, not a "
                "default."
            ),
            evidence=("operator_opt_in:present", "path:present", "app_consent:unobservable"),
        )
    if flag is False:
        return ConsentDecision(
            allowed=False,
            state="consent_withheld",
            detail=(
                "Local persistence is off in WeChat Companion, so no message "
                "text may be written down."
            ),
            evidence=("operator_opt_in:present", "path:present", "app_consent:off"),
        )
    return ConsentDecision(
        allowed=True,
        state="consented",
        detail="Local persistence is on and a memory store was explicitly requested.",
        evidence=("operator_opt_in:present", "path:present", "app_consent:on"),
        database_path=path,
    )
