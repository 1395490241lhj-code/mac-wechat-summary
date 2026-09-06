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

# --- Contract with the Swift store ------------------------------------------

#: Schema versions this bridge understands, read from SQLite's ``user_version``.
#: The macOS app stamps it in ``MessageStore.migrate``. Anything else fails
#: closed: the shape is never inferred from whatever tables happen to exist.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1})

REQUIRED_TABLES: Final[frozenset[str]] = frozenset({"conversations", "messages"})

# --- Environment gate --------------------------------------------------------

ALLOW_READ_ENV: Final = "WECHAT_COMPANION_ALLOW_AGENT_READ"
DB_PATH_ENV: Final = "WECHAT_COMPANION_DB_PATH"

# --- Source selection --------------------------------------------------------

#: Which reader answers. Absent or ``visual`` keeps the historical behaviour:
#: the store the macOS app fills from the visual capture path. ``database``
#: selects an external reader and requires its own explicit configuration.
#: There is no automatic selection and no fallback in either direction.
MESSAGE_SOURCE_ENV: Final = "WECHAT_COMPANION_MESSAGE_SOURCE"

#: Configuration for the external reader, used only when the database source is
#: selected. The executable is never searched for: an absent path means the
#: database source is unavailable, and the visual path is unaffected.
READER_BIN_ENV: Final = "WECHAT_COMPANION_READER_BIN"
READER_CONFIG_ENV: Final = "WECHAT_COMPANION_READER_CONFIG"
READER_TIMEOUT_ENV: Final = "WECHAT_COMPANION_READER_TIMEOUT"
DEFAULT_READER_TIMEOUT: Final = 30.0

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


class BridgeUnavailable(MessageSourceError):
    """Raised when the bridge may not or cannot read.

    The message is a fixed, content-free explanation, safe to return to a
    client and safe to log. It is a ``MessageSourceError`` so that a refusal
    from the store and a refusal from any other source are handled by one
    path and reported to a client identically.
    """


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


def unavailable(error: MessageSourceError) -> dict[str, Any]:
    log(f"request refused: {error.state}")
    return {
        "ok": False,
        "source": selected_source_name(),
        "state": error.state,
        "detail": error.detail,
    }


# --- Sources -----------------------------------------------------------------


class StoreMessageSource:
    """The store the macOS app fills from the visual capture path.

    This is the reader this bridge has always been. The statements, the
    ordering, the clamping and the two opt-ins are unchanged; they have only
    moved behind the same interface every other source implements.
    """

    name = SOURCE_VISUAL

    def status(self) -> SourceStatus:
        try:
            connection, version = open_verified()
        except BridgeUnavailable as error:
            return SourceStatus(
                source=self.name, ready=False, state=error.state, detail=error.detail
            )
        try:
            conversations = connection.execute(
                "SELECT COUNT(*) FROM conversations;"
            ).fetchone()[0]
            messages = connection.execute(
                "SELECT COUNT(*) FROM messages;"
            ).fetchone()[0]
        finally:
            connection.close()
        return SourceStatus(
            source=self.name,
            ready=True,
            state="ready",
            schema_version=version,
            conversation_count=int(conversations),
            message_count=int(messages),
        )

    def list_conversations(self, limit: int) -> list[NormalizedConversation]:
        connection, _ = open_verified()
        try:
            rows = connection.execute(
                """
                SELECT id, title, first_seen_at, last_seen_at
                FROM conversations
                ORDER BY last_seen_at DESC, id DESC
                LIMIT ?;
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
        return [
            NormalizedConversation(
                id=row["id"],
                title=row["title"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
                source=self.name,
            )
            for row in rows
        ]

    def _message(self, row: sqlite3.Row) -> NormalizedMessage:
        """Only the stored structured fields.

        There is deliberately no image, no bubble geometry, no provider detail
        and no SQLite internal here; `normalizedBounds` is not even persisted.
        """
        return NormalizedMessage(
            id=row["id"],
            conversation_id=row["conversation_id"],
            sequence=row["sequence"],
            sender=row["sender"],
            ownership=row["ownership"],
            # The string WeChat displayed. Not a timestamp; never filtered on.
            visible_time=row["visible_time"],
            text=row["text"],
            kind=row["kind"],
            confidence=row["confidence"],
            # When the message was first seen on screen, not when it was sent.
            first_observed_at=row["first_observed_at"],
            source=self.name,
        )

    def get_messages(
        self,
        conversation_id: int,
        limit: int,
        before_sequence: int | None = None,
    ) -> list[NormalizedMessage]:
        connection, _ = open_verified()
        try:
            # Newest-first with the cap applied, then reversed, so a limited
            # read returns the most recent window rather than the oldest one.
            if before_sequence is None:
                rows = connection.execute(
                    """
                    SELECT id, conversation_id, sequence, sender, ownership,
                           visible_time, text, kind, confidence, first_observed_at
                    FROM messages WHERE conversation_id = ?
                    ORDER BY sequence DESC LIMIT ?;
                    """,
                    (conversation_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, conversation_id, sequence, sender, ownership,
                           visible_time, text, kind, confidence, first_observed_at
                    FROM messages WHERE conversation_id = ? AND sequence < ?
                    ORDER BY sequence DESC LIMIT ?;
                    """,
                    (conversation_id, int(before_sequence), limit),
                ).fetchall()
        finally:
            connection.close()
        return [self._message(row) for row in reversed(rows)]

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> list[NormalizedMessage]:
        connection, _ = open_verified()
        try:
            rows = connection.execute(
                """
                SELECT id, conversation_id, sequence, sender, ownership,
                       visible_time, text, kind, confidence, first_observed_at
                FROM messages WHERE first_observed_at >= ?
                ORDER BY first_observed_at ASC, conversation_id ASC, sequence ASC
                LIMIT ?;
                """,
                (since_observed_at, limit),
            ).fetchall()
        finally:
            connection.close()
        return [self._message(row) for row in rows]


def selected_source_name() -> str:
    """Which source is configured, without constructing or contacting it."""
    configured = os.environ.get(MESSAGE_SOURCE_ENV, "").strip().lower()
    return configured or SOURCE_VISUAL


def build_database_source() -> MessageSource:
    """Constructs the external reader source from explicit configuration.

    The adapter is imported here rather than at module scope so that the
    visual path never depends on it being present or importable.
    """
    executable = os.environ.get(READER_BIN_ENV, "").strip()
    if not executable:
        raise BridgeUnavailable(
            "reader_not_configured",
            f"No external reader configured. Set {READER_BIN_ENV} to an explicit path.",
        )
    raw_timeout = os.environ.get(READER_TIMEOUT_ENV, "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_READER_TIMEOUT
    except ValueError:
        timeout = DEFAULT_READER_TIMEOUT
    try:
        from rion_reader_adapter import RionReaderAdapter, RionReaderConfig
    except ImportError as error:  # pragma: no cover - adapter absent
        raise BridgeUnavailable(
            "reader_unavailable", "The external reader adapter is not installed."
        ) from error
    configuration = os.environ.get(READER_CONFIG_ENV, "").strip() or None
    return RionReaderAdapter(
        RionReaderConfig(
            executable=executable,
            config_path=configuration,
            timeout_seconds=max(1.0, timeout),
        )
    )


def active_source() -> MessageSource:
    """The one source that will answer this request.

    Selection is explicit. An unrecognised selection fails closed rather than
    resolving to a default, and no source is ever tried after another one has
    refused: a silent substitution would present one reader's coverage as the
    other's.
    """
    selected = selected_source_name()
    if selected == SOURCE_VISUAL:
        return StoreMessageSource()
    if selected == SOURCE_DATABASE:
        # The agent-read opt-in gates every source, not just the store.
        if os.environ.get(ALLOW_READ_ENV) != "1":
            raise BridgeUnavailable(
                "agent_read_disabled",
                f"Agent read access is off. Set {ALLOW_READ_ENV}=1 to enable it.",
            )
        return build_database_source()
    raise BridgeUnavailable(
        "source_unknown", "The configured message source is not recognised."
    )


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
        "source": selected_source_name(),
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
