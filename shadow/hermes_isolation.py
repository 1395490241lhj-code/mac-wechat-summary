"""Fail-closed isolation guard for every Hermes validation run.

Validating an upstream Hermes tree means importing upstream Hermes modules.
That is not a read-only act: ``hermes_cli.config`` seeds and *upgrades*
``SOUL.md`` inside ``HERMES_HOME`` at **import time**, through
``load_config() -> ensure_hermes_home() -> _ensure_default_soul_md()``. With
``HERMES_HOME`` unset it falls back to ``$HOME/.hermes``, so a bare
``import tui_gateway.server`` is enough to rewrite the operator's real
profile. Current upstream additionally treats the *previous generation* of its
own default ``SOUL.md`` as non-customized content it may replace in place, so
an untouched stock install is exactly the case that gets overwritten.

This module exists because that damage happens before any validation code of
ours runs. It must therefore be called **first** -- before the Hermes tree is
placed on ``sys.path`` and before the first Hermes import -- and it refuses
rather than repairs. There is no "safe default" branch here: an unset variable
is a refusal, not a fallback, because a fallback is precisely the behaviour
that caused the incident.

What it protects:

* the operator's account home (resolved from the *password database*, never
  from ``$HOME``, which is the variable under test);
* ``<account home>/.hermes`` -- the official install and its profile root;
* any path naming the ``wechatshadow`` profile, at any depth.

What it requires: an explicit, absolute, existing, disposable ``HOME`` **and**
``HERMES_HOME``, each outside every protected path. Both are required even
when only one would be consulted, because which one a given Hermes revision
reads is a property of that revision, not of our intent.
"""

from __future__ import annotations

import os
import pwd
from pathlib import Path

#: The profile whose bytes are project evidence and must never be written.
PROTECTED_PROFILE_NAME = "wechatshadow"

#: Variables a caller must set explicitly. Order is the reporting order.
REQUIRED_VARIABLES = ("HOME", "HERMES_HOME")


class IsolationRefused(RuntimeError):
    """A Hermes validation run was refused before it could import anything."""


def account_home() -> Path:
    """The operator's real home, independent of the environment.

    ``$HOME`` is the thing being overridden, so it cannot be the reference
    for deciding what "real" means. The password database is the one source
    a caller cannot redirect.
    """
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def protected_paths() -> tuple[Path, ...]:
    """Paths no validation run may write to, or resolve inside."""
    home = account_home()
    return (home, home / ".hermes", home / ".hermes" / "profiles" / PROTECTED_PROFILE_NAME)


def _refuse(reason: str) -> None:
    raise IsolationRefused(
        f"Hermes validation refused: {reason}. "
        f"Set {' and '.join(REQUIRED_VARIABLES)} to disposable directories "
        "outside the operator's home before importing any Hermes module."
    )


def _check_one(name: str, raw: str | None) -> Path:
    if raw is None or not raw.strip():
        _refuse(f"{name} is not set")
    value = Path(raw)                                    # type: ignore[arg-type]
    if not value.is_absolute():
        _refuse(f"{name} is not an absolute path")
    if not value.is_dir():
        _refuse(f"{name} does not exist as a directory")
    resolved = value.resolve()
    if PROTECTED_PROFILE_NAME in resolved.parts:
        _refuse(f"{name} names the {PROTECTED_PROFILE_NAME} profile")
    for protected in protected_paths():
        # Equality and containment both matter: a disposable HOME must not BE
        # the account home, and a disposable HERMES_HOME must not sit inside
        # it. `is_relative_to` covers the account home itself, so the equality
        # case is included rather than tested separately.
        if resolved == protected or resolved.is_relative_to(protected):
            _refuse(f"{name} resolves inside the protected path {protected}")
    return resolved


def enforce_disposable_home(environ: dict[str, str] | None = None) -> dict[str, Path]:
    """Refuses unless HOME and HERMES_HOME are explicit and disposable.

    Returns the resolved pair on success so a caller can report exactly what
    it validated. Raises :class:`IsolationRefused` otherwise. Call this before
    the Hermes tree reaches ``sys.path``.
    """
    env = os.environ if environ is None else environ
    return {name: _check_one(name, env.get(name)) for name in REQUIRED_VARIABLES}
