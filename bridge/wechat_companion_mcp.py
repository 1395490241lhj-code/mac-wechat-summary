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
"""

from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any, Final

try:  # MCP SDK >= 2.0
    from mcp.server import MCPServer as _Server
except ImportError:  # MCP SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

# --- Contract with the Swift store ------------------------------------------

#: Schema versions this bridge understands, read from SQLite's ``user_version``.
#: The macOS app stamps it in ``MessageStore.migrate``. Anything else fails
#: closed: the shape is never inferred from whatever tables happen to exist.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1})

REQUIRED_TABLES: Final[frozenset[str]] = frozenset({"conversations", "messages"})

# --- Environment gate --------------------------------------------------------

ALLOW_READ_ENV: Final = "WECHAT_COMPANION_ALLOW_AGENT_READ"
DB_PATH_ENV: Final = "WECHAT_COMPANION_DB_PATH"

# --- Limits ------------------------------------------------------------------

MAX_CONVERSATIONS: Final = 200
DEFAULT_CONVERSATIONS: Final = 50
MAX_MESSAGES: Final = 500
DEFAULT_MESSAGES: Final = 100
BUSY_TIMEOUT_MS: Final = 5_000

mcp = _Server("wechat-companion")


def log(message: str) -> None:
    """Diagnostics to stderr only.

    Callers must pass a fixed string or a count. Never interpolate a chat
    title, a sender, or message text: this stream is not private.
    """
    print(message, file=sys.stderr, flush=True)


class BridgeUnavailable(Exception):
    """Raised when the bridge may not or cannot read.

    The message is a fixed, content-free explanation, safe to return to a
    client and safe to log.
    """

    def __init__(self, state: str, detail: str) -> None:
        super().__init__(detail)
        self.state = state
        self.detail = detail


@dataclass(frozen=True)
class Access:
    """A validated, read-only route to the database."""

    path: str

    @property
    def uri(self) -> str:
        # `mode=ro` refuses to create the file and refuses writes. `immutable`
        # is deliberately NOT used: the app may be writing concurrently, and
        # immutable would let us read a torn snapshot.
        from urllib.parse import quote

        return f"file:{quote(self.path)}?mode=ro"


def resolve_access() -> Access:
    """Both opt-ins must be present. There is no default database path."""
    if os.environ.get(ALLOW_READ_ENV) != "1":
        raise BridgeUnavailable(
            "agent_read_disabled",
            f"Agent read access is off. Set {ALLOW_READ_ENV}=1 to enable it.",
        )
    path = os.environ.get(DB_PATH_ENV, "").strip()
    if not path:
        raise BridgeUnavailable(
            "database_not_configured",
            f"No database configured. Set {DB_PATH_ENV} to an explicit path.",
        )
    if not os.path.isfile(path):
        # The path itself is the operator's own configuration, not chat data,
        # but it is still a filesystem detail: report the state, not the path.
        raise BridgeUnavailable(
            "database_missing", "The configured database file does not exist."
        )
    return Access(path=path)


def connect(access: Access) -> sqlite3.Connection:
    """Opens a read-only connection with writes disabled at the engine level."""
    connection = sqlite3.connect(access.uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS};")
    # Belt and braces beside mode=ro: query_only blocks writes even if the
    # connection were ever opened read-write by mistake.
    connection.execute("PRAGMA query_only = ON;")
    return connection


def verify_schema(connection: sqlite3.Connection) -> int:
    """Fails closed on an unrecognised schema rather than guessing."""
    version = int(connection.execute("PRAGMA user_version;").fetchone()[0])
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise BridgeUnavailable(
            "schema_unsupported",
            "Database schema version "
            f"{version} is not supported by this bridge "
            f"(supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}).",
        )
    present = {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table';"
        )
    }
    if not REQUIRED_TABLES.issubset(present):
        raise BridgeUnavailable(
            "schema_incomplete", "The database is missing required tables."
        )
    return version


def open_verified() -> tuple[sqlite3.Connection, int]:
    connection = connect(resolve_access())
    try:
        return connection, verify_schema(connection)
    except BridgeUnavailable:
        connection.close()
        raise


def clamp(value: int | None, default: int, maximum: int) -> int:
    """Caps every caller-supplied limit. A client cannot ask for the world."""
    if value is None:
        return default
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, maximum))


def unavailable(error: BridgeUnavailable) -> dict[str, Any]:
    log(f"request refused: {error.state}")
    return {"ok": False, "state": error.state, "detail": error.detail}


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
    }
    try:
        connection, version = open_verified()
    except BridgeUnavailable as error:
        log(f"status: not ready ({error.state})")
        result.update({"ok": False, "state": error.state, "detail": error.detail})
        return result
    try:
        conversations = connection.execute(
            "SELECT COUNT(*) FROM conversations;"
        ).fetchone()[0]
        messages = connection.execute("SELECT COUNT(*) FROM messages;").fetchone()[0]
    finally:
        connection.close()
    result.update(
        {
            "ok": True,
            "state": "ready",
            "schema_version": version,
            "conversation_count": int(conversations),
            "message_count": int(messages),
        }
    )
    log(f"status: ready, {conversations} conversations, {messages} messages")
    return result


@mcp.tool(description="List stored conversations (id, title, observation times), most recently seen first. Limit is capped.")
def list_conversations(limit: int | None = None) -> dict[str, Any]:
    """List stored conversations, most recently seen first.

    Returns identity and observation metadata only. `limit` is clamped to a
    hard cap.
    """
    try:
        connection, _ = open_verified()
    except BridgeUnavailable as error:
        return unavailable(error)
    capped = clamp(limit, DEFAULT_CONVERSATIONS, MAX_CONVERSATIONS)
    try:
        rows = connection.execute(
            """
            SELECT id, title, first_seen_at, last_seen_at
            FROM conversations
            ORDER BY last_seen_at DESC, id DESC
            LIMIT ?;
            """,
            (capped,),
        ).fetchall()
    finally:
        connection.close()
    log(f"list_conversations: returned {len(rows)} rows (limit {capped})")
    return {
        "ok": True,
        "limit": capped,
        "conversations": [
            {
                "id": row["id"],
                "title": row["title"],
                "first_seen_at": row["first_seen_at"],
                "last_seen_at": row["last_seen_at"],
            }
            for row in rows
        ],
    }


def _message_payload(row: sqlite3.Row) -> dict[str, Any]:
    """Only the stored structured fields.

    There is deliberately no image, no bubble geometry, no provider detail and
    no SQLite internal here; `normalizedBounds` is not even persisted.
    """
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "sequence": row["sequence"],
        "sender": row["sender"],
        "ownership": row["ownership"],
        # The string WeChat displayed. Not a timestamp; never used for filtering.
        "visible_time": row["visible_time"],
        "text": row["text"],
        "kind": row["kind"],
        "confidence": row["confidence"],
        # When the message was first seen on screen, not when it was sent.
        "first_observed_at": row["first_observed_at"],
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
    try:
        connection, _ = open_verified()
    except BridgeUnavailable as error:
        return unavailable(error)
    capped = clamp(limit, DEFAULT_MESSAGES, MAX_MESSAGES)
    try:
        conversation_id = int(conversation_id)
    except (TypeError, ValueError):
        connection.close()
        return {"ok": False, "state": "invalid_argument",
                "detail": "conversation_id must be an integer."}
    try:
        # Newest-first with the cap applied, then reversed, so a limited read
        # returns the most recent window rather than the oldest one.
        if before_sequence is None:
            rows = connection.execute(
                """
                SELECT id, conversation_id, sequence, sender, ownership,
                       visible_time, text, kind, confidence, first_observed_at
                FROM messages WHERE conversation_id = ?
                ORDER BY sequence DESC LIMIT ?;
                """,
                (conversation_id, capped),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT id, conversation_id, sequence, sender, ownership,
                       visible_time, text, kind, confidence, first_observed_at
                FROM messages WHERE conversation_id = ? AND sequence < ?
                ORDER BY sequence DESC LIMIT ?;
                """,
                (conversation_id, int(before_sequence), capped),
            ).fetchall()
    finally:
        connection.close()
    ordered = list(reversed(rows))
    log(f"get_messages: returned {len(ordered)} rows (limit {capped})")
    return {
        "ok": True,
        "conversation_id": conversation_id,
        "limit": capped,
        "next_before_sequence": ordered[0]["sequence"] if ordered else None,
        "messages": [_message_payload(row) for row in ordered],
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
    try:
        connection, _ = open_verified()
    except BridgeUnavailable as error:
        return unavailable(error)
    capped = clamp(limit, DEFAULT_MESSAGES, MAX_MESSAGES)
    try:
        since = float(since_observed_at)
    except (TypeError, ValueError):
        connection.close()
        return {"ok": False, "state": "invalid_argument",
                "detail": "since_observed_at must be a Unix timestamp."}
    try:
        rows = connection.execute(
            """
            SELECT id, conversation_id, sequence, sender, ownership,
                   visible_time, text, kind, confidence, first_observed_at
            FROM messages WHERE first_observed_at >= ?
            ORDER BY first_observed_at ASC, conversation_id ASC, sequence ASC
            LIMIT ?;
            """,
            (since, capped),
        ).fetchall()
    finally:
        connection.close()
    log(f"get_recent_messages: returned {len(rows)} rows (limit {capped})")
    return {
        "ok": True,
        "since_observed_at": since,
        "limit": capped,
        "messages": [_message_payload(row) for row in rows],
    }


def main() -> None:
    # stdio transport: stdout carries MCP framing and nothing else.
    log("wechat-companion MCP bridge starting (read-only)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
