#!/usr/bin/env python3
"""Read-only MCP server over the local memory store (M2.1).

A second, separate server -- **not** the WeChat bridge. The bridge reads the
app's raw store through a ``MessageSource`` and exposes four tools; this
server reads the *memory* store through ``MemoryQueryService`` and exposes
four different tools. They share no process, no code path and no
configuration: ``bridge/`` does not import this package and this file does not
import the bridge. An agent that has been given one has not been given the
other.

What it can do
--------------

Exactly five read-only queries: ``memory_search``, ``memory_timeline``,
``memory_context``, ``memory_recent`` and, since M2.2b, ``memory_conversations``.
The four message queries return the M2 envelope -- items, coverage,
truncated, query scope -- and every item carries its stable citation.
Coverage cannot be dropped on the wire because there is no shape without it.
Discovery returns candidates with their provenance and an honest statement
that it enumerated the store, not WeChat; two candidates with one name stay
two candidates.

What it cannot do
-----------------

Write. The store is opened ``mode=ro`` with ``PRAGMA query_only`` on top, so
a write is refused by the engine. There is no sync tool, no link tool, no
delete tool, and no tool that accepts SQL, a path, or a source policy. The
source policy is the host's conservative default -- every source the store
knows is required -- and the model cannot change it.

Activation
----------

Off unless all three hold, checked on every call through the same gate the
sync uses: ``WECHAT_COMPANION_MEMORY_ENABLED=1``, an explicit
``WECHAT_COMPANION_MEMORY_DB_PATH``, and the app's own ``consent.state``
saying yes. Any refusal is returned as a fixed state token and sentence. No
path, no exception text, no SQL and no host or user identifier is ever in a
response or on stderr.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Final

try:
    from memory_consent import MemoryConsentError, resolve_consent
    from memory_query import (
        DEFAULT_LIMIT,
        ConversationDiscoveryResult,
        MemoryItem,
        MemoryQueryResult,
        MemoryQueryService,
        TimelineCursor,
    )
    from memory_store import MemoryStore, MemoryStoreError
except ImportError:  # pragma: no cover - launched by path from another cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(
        0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bridge")
    )
    from memory_consent import MemoryConsentError, resolve_consent
    from memory_query import (
        DEFAULT_LIMIT,
        ConversationDiscoveryResult,
        MemoryItem,
        MemoryQueryResult,
        MemoryQueryService,
        TimelineCursor,
    )
    from memory_store import MemoryStore, MemoryStoreError

try:  # MCP SDK >= 2.0
    from mcp.server import MCPServer as _Server
except ImportError:  # MCP SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

SERVER_NAME: Final = "wechat_memory"
TOOL_NAMES: Final[tuple[str, ...]] = (
    "memory_search", "memory_timeline", "memory_context", "memory_recent",
    "memory_conversations",
)

# --- Public caps ------------------------------------------------------------
#
# Narrower than the internal API on purpose. A client cannot ask for the world
# and cannot ask for a window of context that is really a whole conversation.

MAX_LIMIT: Final = 200
MAX_CONTEXT_SIDE: Final = 50
DEFAULT_CONTEXT_SIDE: Final = 5
MAX_TEXT_CHARS: Final = 500
MAX_NAME_CHARS: Final = 200
SEARCH_ORDERS: Final = frozenset({"recent", "oldest", "relevance"})
RECENT_ORDERS: Final = frozenset({"recent", "oldest"})

mcp = _Server(SERVER_NAME)


def log(message: str) -> None:
    """stderr, counts and states only. Never content, never a path."""
    print(message, file=sys.stderr, flush=True)


class InvalidArgument(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


# --- Validation ---------------------------------------------------------------


def _int(value: Any, name: str, *, default: int, low: int, high: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidArgument(f"{name} must be an integer.")
    return max(low, min(int(value), high))


def _time(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidArgument(f"{name} must be a Unix timestamp.")
    return float(value)


def _text(value: Any, name: str, *, required: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise InvalidArgument(f"{name} is required.")
        return None
    if not isinstance(value, str):
        raise InvalidArgument(f"{name} must be a string.")
    if len(value) > MAX_TEXT_CHARS:
        raise InvalidArgument(f"{name} is too long.")
    return value


def _order(value: Any, allowed: frozenset[str], default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or value not in allowed:
        raise InvalidArgument("order must be one of: " + ", ".join(sorted(allowed)) + ".")
    return value


def _window(start: float | None, end: float | None) -> None:
    if start is not None and end is not None and start > end:
        raise InvalidArgument("start must not be after end.")


def _cursor(value: Any, name: str) -> TimelineCursor | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidArgument(f"{name} must be a cursor object from a previous timeline item.")
    timestamp = value.get("timestamp")
    sequence = value.get("sequence")
    canonical = value.get("canonical_id")
    if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or isinstance(sequence, bool) or not isinstance(sequence, int)
            or not isinstance(canonical, str) or not canonical):
        raise InvalidArgument(f"{name} is not a valid cursor.")
    return TimelineCursor(timestamp=float(timestamp), sequence=sequence, canonical_id=canonical)


# --- Projection ---------------------------------------------------------------


def _item(item: MemoryItem) -> dict[str, Any]:
    return {
        "citation": item.citation.as_dict(),
        "logical_message_id": item.logical_message_id,
        "sender": item.sender,
        "ownership": item.ownership,
        "kind": item.kind,
        "text": item.text,
        "timestamp": item.timestamp,
        "is_focal": item.is_focal,
        "cursor": {
            "timestamp": item.cursor.timestamp,
            "sequence": item.cursor.sequence,
            "canonical_id": item.cursor.canonical_id,
        },
        "observations": [
            {
                "citation": o.citation.as_dict(),
                "sender": o.sender,
                "ownership": o.ownership,
                "kind": o.kind,
                "text": o.text,
                "visible_time": o.visible_time,
                "confidence": o.confidence,
                "observation_count": o.observation_count,
            }
            for o in item.observations
        ],
    }


def envelope(result: MemoryQueryResult) -> dict[str, Any]:
    """The wire shape. Coverage and scope are not optional keys."""
    scope = result.query_scope
    return {
        "ok": True,
        "items": [_item(i) for i in result.items],
        "coverage": result.coverage.as_dict(),
        "truncated": result.truncated,
        "query_scope": {
            "kind": scope.kind,
            "conversation_canonical_id": scope.conversation_canonical_id,
            "window": list(scope.window),
            "limit": scope.limit,
            "order": scope.order,
            "text": scope.text,
            "sender": scope.sender,
            "anchor": scope.anchor,
            # Host-controlled. Reported so the reader knows what "trustworthy"
            # was measured against; not accepted as input anywhere.
            "policy": {
                "required_sources": list(scope.policy.required),
                "supplemental_sources": list(scope.policy.supplemental),
            },
        },
        "focal_canonical_id": result.focal_canonical_id,
    }


def discovery_envelope(result: ConversationDiscoveryResult) -> dict[str, Any]:
    """The discovery wire shape. Truncation and scope are not optional keys."""
    scope = result.query_scope
    return {
        "ok": True,
        "items": [i.as_dict() for i in result.items],
        "truncated": result.truncated,
        "query_scope": {
            "kind": scope.kind,
            "name": scope.text,
            "limit": scope.limit,
            "order": scope.order,
            "match_rule": "exact (normalised equality) before contains (normalised substring); no fuzzy matching",
        },
        "coverage": result.coverage.as_dict(),
        "candidates": len(result.items),
        "unique": result.is_unique,
        "ambiguous": result.is_ambiguous,
    }


def refusal(state: str, detail: str) -> dict[str, Any]:
    log(f"request refused: {state}")
    return {"ok": False, "state": state, "detail": detail}


# --- The store, per call --------------------------------------------------------


def _service() -> tuple[MemoryStore, MemoryQueryService]:
    """Consent, then a read-only store, on every call.

    Per call rather than at startup so a consent withdrawn while the server is
    running takes effect on the next question, and so a refusal at startup
    does not need a different shape from a refusal later.
    """
    store = MemoryStore.open_read_only(resolve_consent())
    return store, MemoryQueryService(store)


def _answer(run) -> dict[str, Any]:
    try:
        store, service = _service()
    except MemoryConsentError as error:
        return refusal(error.state, error.detail)
    except MemoryStoreError as error:
        return refusal(error.state, error.detail)
    try:
        result = run(service)
    except InvalidArgument as error:
        return refusal("invalid_argument", error.detail)
    except MemoryStoreError as error:
        return refusal(error.state, error.detail)
    except Exception:  # noqa: BLE001 - never let exception prose reach a client
        return refusal("internal_error", "The memory server could not answer this request.")
    finally:
        store.close()
    log(f"{result.query_scope.kind}: returned {len(result.items)} item(s), "
        f"coverage {result.coverage.status}")
    if isinstance(result, ConversationDiscoveryResult):
        return discovery_envelope(result)
    return envelope(result)


# --- Tools --------------------------------------------------------------------


@mcp.tool(description="Search remembered messages. Deterministic local full-text search with optional conversation, sender and time filters. Every item carries a citation; every response carries coverage. Accepts no SQL.")
def memory_search(
    text: str | None = None,
    conversation_id: str | None = None,
    sender: str | None = None,
    start: float | None = None,
    end: float | None = None,
    limit: int | None = None,
    order: str | None = None,
) -> dict[str, Any]:
    """`order`: recent (default), oldest, or relevance (needs text)."""
    def run(service: MemoryQueryService) -> MemoryQueryResult:
        query_text = _text(text, "text")
        chosen = _order(order, SEARCH_ORDERS, "recent")
        if chosen == "relevance" and query_text is None:
            raise InvalidArgument("relevance ordering requires text.")
        s, e = _time(start, "start"), _time(end, "end")
        _window(s, e)
        return service.search(
            text=query_text,
            conversation_canonical_id=_text(conversation_id, "conversation_id"),
            sender=_text(sender, "sender"),
            start=s, end=e,
            limit=_int(limit, "limit", default=DEFAULT_LIMIT, low=1, high=MAX_LIMIT),
            order=chosen,  # type: ignore[arg-type]
        )
    return _answer(run)


@mcp.tool(description="Read one remembered conversation in stable chronological order (timestamp, sequence, id). Page with the after_cursor / before_cursor objects from returned items. Independent of text search.")
def memory_timeline(
    conversation_id: str,
    start: float | None = None,
    end: float | None = None,
    after_cursor: dict | None = None,
    before_cursor: dict | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    def run(service: MemoryQueryService) -> MemoryQueryResult:
        s, e = _time(start, "start"), _time(end, "end")
        _window(s, e)
        return service.timeline(
            _text(conversation_id, "conversation_id", required=True) or "",
            start=s, end=e,
            after=_cursor(after_cursor, "after_cursor"),
            before=_cursor(before_cursor, "before_cursor"),
            limit=_int(limit, "limit", default=DEFAULT_LIMIT, low=1, high=MAX_LIMIT),
        )
    return _answer(run)


@mcp.tool(description="Read the messages immediately before and after one remembered message (by canonical message id), within the same conversation. The focal item is marked. Fails clearly on an unknown id.")
def memory_context(
    message_id: str,
    before: int | None = None,
    after: int | None = None,
) -> dict[str, Any]:
    def run(service: MemoryQueryService) -> MemoryQueryResult:
        return service.context_around(
            _text(message_id, "message_id", required=True) or "",
            before=_int(before, "before", default=DEFAULT_CONTEXT_SIDE, low=0, high=MAX_CONTEXT_SIDE),
            after=_int(after, "after", default=DEFAULT_CONTEXT_SIDE, low=0, high=MAX_CONTEXT_SIDE),
        )
    return _answer(run)


@mcp.tool(description="Read the most recent remembered messages, optionally within one conversation or a time window. `order`: recent (newest first, default) or oldest (same newest set, chronological).")
def memory_recent(
    conversation_id: str | None = None,
    since: float | None = None,
    until: float | None = None,
    limit: int | None = None,
    order: str | None = None,
) -> dict[str, Any]:
    def run(service: MemoryQueryService) -> MemoryQueryResult:
        s, u = _time(since, "since"), _time(until, "until")
        _window(s, u)
        return service.recent_context(
            conversation_canonical_id=_text(conversation_id, "conversation_id"),
            since=s, until=u,
            limit=_int(limit, "limit", default=DEFAULT_LIMIT, low=1, high=MAX_LIMIT),
            order=_order(order, RECENT_ORDERS, "recent"),  # type: ignore[arg-type]
        )
    return _answer(run)


@mcp.tool(description="Find remembered conversations by display name, to obtain the canonical_conversation_id for memory_search / memory_timeline / memory_recent. Exact (normalised) matches rank first, then substring matches; no fuzzy matching. Several candidates with one name stay several: choose, or ask, never guess. With no name, lists conversations most recently seen first.")
def memory_conversations(
    name: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    def run(service: MemoryQueryService) -> ConversationDiscoveryResult:
        query = _text(name, "name")
        if query is not None and len(query) > MAX_NAME_CHARS:
            raise InvalidArgument("name is too long.")
        return service.conversations(
            name=query,
            limit=_int(limit, "limit", default=DEFAULT_LIMIT, low=1, high=MAX_LIMIT),
        )
    return _answer(run)


def main() -> None:
    log("wechat-memory MCP server starting (read-only)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
