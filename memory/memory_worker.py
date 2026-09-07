#!/usr/bin/env python3
"""The bundled local memory worker the macOS app runs (M2.2d).

The app is Swift; the memory layer is Python. Rather than ask the user to
install an interpreter, the app ships this module frozen into a self-contained
executable and runs it as a one-shot child process for one explicit,
foreground action at a time.

Protocol
--------

One JSON object in on stdin, one JSON object out on stdout, then exit. There
is no shell, no command string, no second request, and no way to name an
operation that is not in :data:`OPERATIONS`::

    {"op": "sync",   "store_path": "<optional>", "conversation_limit": 50, "message_limit": 200}
    {"op": "status", "store_path": "<optional>"}
    {"op": "paths"}

    {"ok": true,  "op": "sync", "state": "synced", "counts": {...}, "freshness": {...}}
    {"ok": false, "op": "sync", "state": "consent_withheld", "detail": "..."}

Exit codes: ``0`` the operation succeeded, ``1`` a structured refusal or
failure (the body says which, in fixed tokens), ``2`` the request itself was
not usable. Anything on stderr is a fixed token; stdout is the protocol and
carries nothing else.

What it does not do
-------------------

No network, no provider SDK, no agent, no MCP, no shell, no arbitrary
command, no scheduler. It reuses :mod:`memory_sync` and therefore the M1
ingestor -- there is exactly one ingestion implementation in this project and
this is not a second one. It never emits an absolute path: the store's
location is derived, not reported, and ``paths`` answers with the form
relative to the user's home so a caller can show *where* without disclosing
*whose*.

Consent is enforced here as well as in the app. The app checks its own
consent before spawning; this process independently resolves the app-owned
consent state through :func:`memory_consent.resolve_consent`, so a worker
started by anything else still refuses. Activation variables alone never
authorise persistence -- they say which store, while the app's recorded
consent says whether at all.
"""

from __future__ import annotations

import json
import sys
from typing import Any

try:
    import memory_consent as consent
    from memory_freshness import memory_freshness
    from memory_paths import (
        canonical_message_store_path,
        canonical_store_path,
        prepare_store_directory,
        relative_store_path,
    )
    from memory_store import MemoryStore, MemoryStoreError
    from memory_sync import build_selected_source, sync_from_source
    from message_source import (
        MESSAGE_SOURCE_ENV,
        READER_BIN_ENV,
        READER_CONFIG_ENV,
        READER_TIMEOUT_ENV,
        SOURCE_NAMES,
        MessageSourceError,
    )
except ImportError:  # pragma: no cover - source checkout, run by path
    import os

    _HERE = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, _HERE)
    sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "bridge"))
    import memory_consent as consent
    from memory_freshness import memory_freshness
    from memory_paths import (
        canonical_message_store_path,
        canonical_store_path,
        prepare_store_directory,
        relative_store_path,
    )
    from memory_store import MemoryStore, MemoryStoreError
    from memory_sync import build_selected_source, sync_from_source
    from message_source import (
        MESSAGE_SOURCE_ENV,
        READER_BIN_ENV,
        READER_CONFIG_ENV,
        READER_TIMEOUT_ENV,
        SOURCE_NAMES,
        MessageSourceError,
    )

#: The bridge's read opt-in. The app reading its *own* store to fill its own
#: memory is not an agent read, but it goes through the bridge's one reader
#: rule rather than a second one, so the worker sets the opt-in explicitly for
#: the store it was told to read. Nothing is inherited from the parent
#: environment: the child's activation is built here, from a validated
#: request, and from nothing else.
ALLOW_READ_ENV: str = "WECHAT_COMPANION_ALLOW_AGENT_READ"
DB_PATH_ENV: str = "WECHAT_COMPANION_DB_PATH"

OPERATIONS: frozenset[str] = frozenset({"sync", "status", "paths"})

#: Largest request accepted. A request is a handful of fields; anything larger
#: is a mistake or an attempt to make this process do something else.
MAX_REQUEST_BYTES: int = 64 * 1024

MAX_CONVERSATION_LIMIT: int = 500
MAX_MESSAGE_LIMIT: int = 2_000

EXIT_OK: int = 0
EXIT_REFUSED: int = 1
EXIT_BAD_REQUEST: int = 2


class BadRequest(Exception):
    """The request could not be understood. Fixed text; never echoes input."""


def _limit(request: dict[str, Any], key: str, default: int, maximum: int) -> int:
    value = request.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BadRequest(f"{key} must be an integer.")
    return max(1, min(value, maximum))


def _store_path(request: dict[str, Any]) -> str:
    """The store to act on: what the app named, else the canonical location.

    A caller may name a path -- the app does, so one owner decides where the
    store lives -- but nothing is created merely by naming it.
    """
    given = request.get("store_path")
    if given is None:
        return str(canonical_store_path())
    if not isinstance(given, str) or not given.strip():
        raise BadRequest("store_path must be a non-empty string when present.")
    return given


def _activation(store_path: str) -> dict[str, str]:
    """The activation half of the gate. Says *which* store, never *whether*."""
    return {
        consent.MEMORY_ENABLED_ENV: "1",
        consent.MEMORY_DB_PATH_ENV: store_path,
    }


def _apply_source_activation(request: dict[str, Any]) -> str:
    """Sets this process's source activation from the request. Returns the name.

    The bridge's selection rule is then applied to it verbatim by
    ``active_source()``: absent selection is the visual store, ``database``
    needs an injected reader, an unknown name fails closed, and there is no
    fallback in either direction. A selection this worker cannot honour
    becomes a refusal, never a read of a different source.
    """
    import os

    selection = request.get("message_source")
    if selection is not None and (not isinstance(selection, str) or selection not in SOURCE_NAMES):
        raise BadRequest("message_source must be one of: " + ", ".join(sorted(SOURCE_NAMES)) + ".")
    store = request.get("message_store_path")
    if store is not None and (not isinstance(store, str) or not store.strip()):
        raise BadRequest("message_store_path must be a non-empty string when present.")

    os.environ[ALLOW_READ_ENV] = "1"
    os.environ[DB_PATH_ENV] = store or str(canonical_message_store_path())
    for key, value in (
        (MESSAGE_SOURCE_ENV, selection),
        (READER_BIN_ENV, request.get("reader_bin")),
        (READER_CONFIG_ENV, request.get("reader_config")),
        (READER_TIMEOUT_ENV, request.get("reader_timeout")),
    ):
        if value is None:
            os.environ.pop(key, None)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            os.environ[key] = str(value)
        else:
            raise BadRequest("Reader options must be strings or numbers.")
    return selection or "visual"


def _refusal(op: str, state: str, detail: str) -> dict[str, Any]:
    return {"ok": False, "op": op, "state": state, "detail": detail}


def handle(request: dict[str, Any], *, read_app_consent_state=None) -> tuple[dict[str, Any], int]:
    """Runs one operation and returns its response and exit code."""
    op = request.get("op")
    if not isinstance(op, str) or op not in OPERATIONS:
        raise BadRequest("op must be one of: " + ", ".join(sorted(OPERATIONS)) + ".")
    reader = read_app_consent_state or consent.read_app_consent_state_macos

    if op == "paths":
        # Deliberately relative: where the store lives, not whose home it is in.
        return {"ok": True, "op": op, "state": "ok",
                "relative_store_path": relative_store_path()}, EXIT_OK

    store_path = _store_path(request)
    decision = consent.resolve_consent(_activation(store_path), reader)
    if not decision.allowed:
        return _refusal(op, decision.state, decision.detail), EXIT_REFUSED

    if op == "status":
        try:
            store = MemoryStore.open_read_only(decision)
        except MemoryStoreError as error:
            return _refusal(op, error.state, error.detail), EXIT_REFUSED
        try:
            import time

            fresh = memory_freshness(store, generated_at=time.time()).as_dict()
        finally:
            store.close()
        return {"ok": True, "op": op, "state": "ok",
                "consent_generation": decision.consent_generation,
                "freshness": fresh}, EXIT_OK

    # op == "sync"
    conversation_limit = _limit(request, "conversation_limit", 50, MAX_CONVERSATION_LIMIT)
    message_limit = _limit(request, "message_limit", 200, MAX_MESSAGE_LIMIT)
    selected = _apply_source_activation(request)
    try:
        source = build_selected_source()
    except MessageSourceError as error:
        # The selected source cannot be built. Never substitute another one.
        return _refusal(op, f"{selected}:{error.state}", error.detail), EXIT_REFUSED
    # Consent has been resolved; only now may a directory exist.
    prepare_store_directory(store_path)
    report = sync_from_source(
        source,
        environment=_activation(store_path),
        read_app_consent_state=reader,
        conversation_limit=conversation_limit,
        message_limit=message_limit,
    )
    summary = report.summary()
    if not report.ok:
        state = summary.get("failure_state") or summary.get("state") or "sync_failed"
        return _refusal(op, str(state), "The memory sync did not complete."), EXIT_REFUSED
    return {
        "ok": True, "op": op, "state": "synced", "source": summary.get("source"),
        "counts": {
            "conversations_seen": summary.get("conversations_seen", 0),
            "messages_seen": summary.get("messages_seen", 0),
            "messages_inserted": summary.get("messages_inserted", 0),
            "messages_updated": summary.get("messages_updated", 0),
            "duplicates_detected": summary.get("duplicates_detected", 0),
        },
        "consent_generation": summary.get("consent_generation"),
        "freshness": summary.get("freshness"),
    }, EXIT_OK


def main(stdin=None, stdout=None, *, read_app_consent_state=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    try:
        raw = stdin.read(MAX_REQUEST_BYTES + 1)
    except OSError:
        raw = ""
    if len(raw) > MAX_REQUEST_BYTES:
        json.dump({"ok": False, "op": None, "state": "request_too_large",
                   "detail": "The request exceeds the accepted size."}, stdout)
        return EXIT_BAD_REQUEST
    try:
        request = json.loads(raw or "{}")
        if not isinstance(request, dict):
            raise BadRequest("The request must be a JSON object.")
        response, code = handle(request, read_app_consent_state=read_app_consent_state)
    except json.JSONDecodeError:
        json.dump({"ok": False, "op": None, "state": "malformed_request",
                   "detail": "The request was not valid JSON."}, stdout)
        return EXIT_BAD_REQUEST
    except BadRequest as error:
        json.dump({"ok": False, "op": None, "state": "invalid_request",
                   "detail": str(error)}, stdout)
        return EXIT_BAD_REQUEST
    except Exception:  # noqa: BLE001 - never let exception prose reach the caller
        json.dump({"ok": False, "op": None, "state": "internal_error",
                   "detail": "The memory worker could not complete the request."}, stdout)
        return EXIT_REFUSED
    json.dump(response, stdout)
    return code


if __name__ == "__main__":  # pragma: no cover - the frozen entry point
    sys.exit(main())
