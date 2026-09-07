#!/usr/bin/env python3
"""Read-only MCP bridge over the WeChat Companion message store.

This server exposes reconciled, message-level chat data that the macOS app has
already persisted with the user's explicit local-storage consent. It is
strictly a reader:

* The database is opened through a SQLite URI with ``mode=ro`` and every
  connection additionally sets ``PRAGMA query_only=ON``, so a write is rejected
  by the engine itself rather than only by convention.
* There is no tool that accepts SQL, a filter expression, or a filesystem path.
  Every query in this file is a fixed statement with bound parameters.
* Access is denied unless the operator has opted in twice: an explicit
  ``WECHAT_COMPANION_ALLOW_AGENT_READ=1`` and an explicit
  ``WECHAT_COMPANION_DB_PATH``. There is no default path, so the production
  store is never reachable by accident.

Privacy: stdout belongs to the MCP protocol alone. Diagnostics go to stderr and
are limited to counts, states and error categories -- never a chat title, a
sender, or message text.

The four tools no longer read SQLite directly. They ask a ``MessageSource``,
and the store this file has always read is one such source. A second source can
therefore exist without any tool, the agent runner, or the skill changing: the
tool names, their arguments and the shape of a message are identical whichever
source answered. Which source answered is reported on the response envelope,
because a coverage difference between readers must never be silent. Selection
is explicit and there is no fallback between sources: if the selected one
cannot answer, the request fails rather than being quietly served by the other.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any, Final

try:
    from message_source import (
        MESSAGE_SOURCE_ENV as _MESSAGE_SOURCE_ENV,
        READER_BIN_ENV as _READER_BIN_ENV,
        READER_CONFIG_ENV as _READER_CONFIG_ENV,
        READER_TIMEOUT_ENV as _READER_TIMEOUT_ENV,
        SOURCE_DATABASE,
        SOURCE_VISUAL,
        MessageSource,
        MessageSourceError,
        NormalizedConversation,
        NormalizedMessage,
        SourceStatus,
    )
except ImportError:  # pragma: no cover - launched by path from another cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from message_source import (
        MESSAGE_SOURCE_ENV as _MESSAGE_SOURCE_ENV,
        READER_BIN_ENV as _READER_BIN_ENV,
        READER_CONFIG_ENV as _READER_CONFIG_ENV,
        READER_TIMEOUT_ENV as _READER_TIMEOUT_ENV,
        SOURCE_DATABASE,
        SOURCE_VISUAL,
        MessageSource,
        MessageSourceError,
        NormalizedConversation,
        NormalizedMessage,
        SourceStatus,
    )

try:  # MCP SDK >= 2.0
    from mcp.server import MCPServer as _Server
except ImportError:  # MCP SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

# --- Store access and source selection --------------------------------------
#
# Everything below the tools lives in ``store_access`` so that a process
# without the MCP SDK -- the app's bundled memory worker -- can use exactly
# this code rather than a second copy of it. These names are re-exported
# unchanged; this module's behaviour, tool set and wire shape are untouched.

try:
    from store_access import (
        ALLOW_READ_ENV,
        BUSY_TIMEOUT_MS,
        DB_PATH_ENV,
        DEFAULT_READER_TIMEOUT,
        MESSAGE_SOURCE_ENV,
        READER_BIN_ENV,
        READER_CONFIG_ENV,
        READER_TIMEOUT_ENV,
        REQUIRED_TABLES,
        SUPPORTED_SCHEMA_VERSIONS,
        Access,
        BridgeUnavailable,
        StoreMessageSource,
        active_source,
        build_database_source,
        connect,
        log,
        open_verified,
        resolve_access,
        selected_source_name,
        verify_schema,
    )
except ImportError:  # pragma: no cover - launched by path from another cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from store_access import (  # type: ignore[no-redef]
        ALLOW_READ_ENV,
        BUSY_TIMEOUT_MS,
        DB_PATH_ENV,
        DEFAULT_READER_TIMEOUT,
        MESSAGE_SOURCE_ENV,
        READER_BIN_ENV,
        READER_CONFIG_ENV,
        READER_TIMEOUT_ENV,
        REQUIRED_TABLES,
        SUPPORTED_SCHEMA_VERSIONS,
        Access,
        BridgeUnavailable,
        StoreMessageSource,
        active_source,
        build_database_source,
        connect,
        log,
        open_verified,
        resolve_access,
        selected_source_name,
        verify_schema,
    )

# --- Limits ------------------------------------------------------------------

MAX_CONVERSATIONS: Final = 200
DEFAULT_CONVERSATIONS: Final = 50
MAX_MESSAGES: Final = 500
DEFAULT_MESSAGES: Final = 100

mcp = _Server("wechat-companion")

def clamp(value: int | None, default: int, maximum: int) -> int:
    """Caps every caller-supplied limit. A client cannot ask for the world."""
    if value is None:
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


def unavailable(error: MessageSourceError) -> dict[str, Any]:
    log(f"request refused: {error.state}")
    return {
        "ok": False,
        "source": selected_source_name(),
        "state": error.state,
        "detail": error.detail,
    }


# --- Tools -------------------------------------------------------------------


@mcp.tool(description="Report whether the bridge, database and schema are ready. Returns no chat or message content.")
def status() -> dict[str, Any]:
    """Report whether the bridge, database and schema are ready.

    Returns no chat or message content of any kind -- only readiness states,
    the schema version and aggregate counts.
    """
    result: dict[str, Any] = {
        "bridge": "ready",
        "read_only": True,
        "agent_read_enabled": os.environ.get(ALLOW_READ_ENV) == "1",
        "database_configured": bool(os.environ.get(DB_PATH_ENV, "").strip()),
        "supported_schema_versions": sorted(SUPPORTED_SCHEMA_VERSIONS),
        # Which reader is selected, and whether each one's configuration is
        # present at all. Both are booleans and a name: a configured location
        # is never disclosed, only the fact that one was supplied.
        "source": selected_source_name(),
        "reader_configured": bool(os.environ.get(READER_BIN_ENV, "").strip()),
    }
    try:
        report = active_source().status()
    except MessageSourceError as error:
        log(f"status: not ready ({error.state})")
        result.update({"ok": False, "state": error.state, "detail": error.detail})
        return result
    if not report.ready:
        log(f"status: not ready ({report.state})")
        result.update(
            {"ok": False, "state": report.state, "detail": report.detail}
        )
        return result
    result.update({"ok": True, **report.payload()})
    log(f"status: ready, source {report.source}")
    return result


@mcp.tool(description="List stored conversations (id, title, observation times), most recently seen first. Limit is capped.")
def list_conversations(limit: int | None = None) -> dict[str, Any]:
    """List stored conversations, most recently seen first.

    Returns identity and observation metadata only. `limit` is clamped to a
    hard cap.
    """
    capped = clamp(limit, DEFAULT_CONVERSATIONS, MAX_CONVERSATIONS)
    try:
        source = active_source()
        conversations = source.list_conversations(capped)
    except MessageSourceError as error:
        return unavailable(error)
    log(f"list_conversations: returned {len(conversations)} rows (limit {capped})")
    return {
        "ok": True,
        "source": source.name,
        "limit": capped,
        "conversations": [item.payload() for item in conversations],
    }


@mcp.tool(description="Read one conversation's messages ordered by their stored sequence. Page backwards with before_sequence. Accepts no SQL.")
def get_messages(
    conversation_id: int,
    limit: int | None = None,
    before_sequence: int | None = None,
) -> dict[str, Any]:
    """Read one conversation's messages in stored order.

    Ordered by `sequence`, which is the conversation's own stable ordering.
    Pass `before_sequence` to page backwards into older messages. Accepts no
    SQL and no filter expression.
    """
    capped = clamp(limit, DEFAULT_MESSAGES, MAX_MESSAGES)
    try:
        conversation_id = int(conversation_id)
    except (TypeError, ValueError):
        return {"ok": False, "source": selected_source_name(),
                "state": "invalid_argument",
                "detail": "conversation_id must be an integer."}
    try:
        source = active_source()
        ordered = source.get_messages(conversation_id, capped, before_sequence)
    except MessageSourceError as error:
        return unavailable(error)
    log(f"get_messages: returned {len(ordered)} rows (limit {capped})")
    return {
        "ok": True,
        "source": source.name,
        "conversation_id": conversation_id,
        "limit": capped,
        "next_before_sequence": ordered[0].sequence if ordered else None,
        "messages": [item.payload() for item in ordered],
    }


@mcp.tool(description="Read messages first observed at or after a Unix timestamp. Filters on first_observed_at, never on the visible time string.")
def get_recent_messages(
    since_observed_at: float, limit: int | None = None
) -> dict[str, Any]:
    """Read messages first observed at or after a Unix timestamp.

    Filters on `first_observed_at` -- when the message was actually seen on
    screen. It deliberately never filters on `visible_time`, which is a display
    string like "昨天 14:30" and carries no reliable date.
    """
    capped = clamp(limit, DEFAULT_MESSAGES, MAX_MESSAGES)
    try:
        since = float(since_observed_at)
    except (TypeError, ValueError):
        return {"ok": False, "source": selected_source_name(),
                "state": "invalid_argument",
                "detail": "since_observed_at must be a Unix timestamp."}
    try:
        source = active_source()
        rows = source.get_recent_messages(since, capped)
    except MessageSourceError as error:
        return unavailable(error)
    log(f"get_recent_messages: returned {len(rows)} rows (limit {capped})")
    return {
        "ok": True,
        "source": source.name,
        "since_observed_at": since,
        "limit": capped,
        "messages": [item.payload() for item in rows],
    }


def main() -> None:
    # stdio transport: stdout carries MCP framing and nothing else.
    log("wechat-companion MCP bridge starting (read-only)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
