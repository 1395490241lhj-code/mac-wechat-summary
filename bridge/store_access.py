"""Read-only access to the app's message store, and which source answers.

Extracted from ``wechat_companion_mcp.py`` unchanged so that the same code can
be used by a process that must not depend on the MCP SDK -- the bundled memory
worker the macOS app runs (M2.2d). The MCP bridge imports every name from
here and re-exports it, so its tools, its behaviour and its tests are exactly
what they were; this module simply removes the SDK from the import path of
everything below the tools.

Nothing here is new. The two opt-ins, the ``mode=ro`` + ``query_only``
connection, the fail-closed schema check, the fixed statements, the source
selection with **no fallback in either direction** -- all are the bridge's
rules, in the bridge's words, moved one file down.
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
        COVERAGE_COMPLETE,
        COVERAGE_PARTIAL,
        REASON_CALLER_LIMIT,
        REASON_EMPTY_WINDOW,
        REASON_FULL_WINDOW_OBSERVED,
        REASON_TIMESTAMP_MISMATCH,
        SOURCE_DATABASE,
        SOURCE_VISUAL,
        MessageSource,
        MessageSourceError,
        NormalizedConversation,
        NormalizedMessage,
        ReadCoverage,
        ReadFreshness,
        ReadResult,
        SourceStatus,
    )
except ImportError:  # pragma: no cover - imported by path from another cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from message_source import (
        MESSAGE_SOURCE_ENV as _MESSAGE_SOURCE_ENV,
        READER_BIN_ENV as _READER_BIN_ENV,
        READER_CONFIG_ENV as _READER_CONFIG_ENV,
        READER_TIMEOUT_ENV as _READER_TIMEOUT_ENV,
        COVERAGE_COMPLETE,
        COVERAGE_PARTIAL,
        REASON_CALLER_LIMIT,
        REASON_EMPTY_WINDOW,
        REASON_FULL_WINDOW_OBSERVED,
        REASON_TIMESTAMP_MISMATCH,
        SOURCE_DATABASE,
        SOURCE_VISUAL,
        MessageSource,
        MessageSourceError,
        NormalizedConversation,
        NormalizedMessage,
        ReadCoverage,
        ReadFreshness,
        ReadResult,
        SourceStatus,
    )

# --- Contract with the Swift store ------------------------------------------

#: Schema versions this bridge understands, read from SQLite's ``user_version``.
#:
#: The contract verified here is **the version stamp plus the required table
#: names**, and deliberately nothing more: no column, CHECK, index or foreign
#: key is inspected. Every query in this module is a fixed statement over those
#: tables, so checking more would couple the reader to details it never reads
#: and would fail on a benign additive column. The writer may be stricter.
#: The macOS app stamps it in ``MessageStore.migrate``. Anything else fails
#: closed: the shape is never inferred from whatever tables happen to exist.
SUPPORTED_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1, 2})

#: What each version promises, checked against the stamp rather than guessed
#: from the file. Widening the accepted versions alone is not enough: a database
#: stamped 2 whose archive tables are absent would otherwise be accepted, and
#: the stamp would assert a shape the file does not have.
#:
#: Version 2 adds archive evidence tables. They are **not** exposed through any
#: tool here -- the four message tools stay visual-only on both versions -- but
#: a file claiming to be v2 must actually have them.
REQUIRED_TABLES_BY_VERSION: Final[dict[int, frozenset[str]]] = {
    1: frozenset({"conversations", "messages"}),
    2: frozenset({
        "conversations",
        "messages",
        "archive_conversations",
        "archive_imports",
        "archive_attributed_records",
        "archive_unattributed_records",
    }),
}

#: The v1 set, kept for callers that predate the per-version mapping.
REQUIRED_TABLES: Final[frozenset[str]] = REQUIRED_TABLES_BY_VERSION[1]

# --- Environment gate --------------------------------------------------------

ALLOW_READ_ENV: Final = "WECHAT_COMPANION_ALLOW_AGENT_READ"
DB_PATH_ENV: Final = "WECHAT_COMPANION_DB_PATH"

# --- Source selection --------------------------------------------------------
#
# The variable names come from ``message_source`` so that this reader and any
# launcher that activates a source cannot drift apart. Absent selection keeps
# the historical behaviour: the store the macOS app fills from the visual
# capture path. Selecting the database source requires its own explicit
# configuration; there is no automatic selection, no discovery, and no fallback
# in either direction.

MESSAGE_SOURCE_ENV: Final = _MESSAGE_SOURCE_ENV
READER_BIN_ENV: Final = _READER_BIN_ENV
READER_CONFIG_ENV: Final = _READER_CONFIG_ENV
READER_TIMEOUT_ENV: Final = _READER_TIMEOUT_ENV
DEFAULT_READER_TIMEOUT: Final = 30.0

BUSY_TIMEOUT_MS: Final = 5_000

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
    # Subset, not equality: an unknown additive table is tolerated, which is
    # what keeps future additive versions cheap. The version is never inferred
    # from the tables -- the stamp is the claim, and these are checked against
    # it.
    if not REQUIRED_TABLES_BY_VERSION[version].issubset(present):
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


# --- Sources -----------------------------------------------------------------


def _newest(moments: Any) -> float | None:
    """The newest moment actually returned, or ``None`` when nothing was."""
    known = [moment for moment in moments if moment is not None]
    return max(known) if known else None


def _conservative_coverage(
    *,
    item_count: int,
    observed: float | None,
    filled: bool,
    newest: float | None,
    requested_start: float | None = None,
) -> ReadCoverage:
    """What a visual read may claim, and no more.

    Visual capture observes what was on screen; the absence of a row is not
    evidence that a message does not exist. So a filled limit is always
    partial, and a short answer is complete only when an independent query
    for the store's newest recorded moment in the *exact* requested scope
    shows this read reached it. ``newest`` is that query's answer; it is
    never derived from the items, which is the whole point of asking again.

    The two reads are not one transaction. If the store advanced between
    them, ``newest`` runs ahead of ``observed`` and that is reported as a
    mismatch -- evidence about currency, never repaired, filtered or retried.
    """
    if filled:
        # Full answer. The store could hold one more for all this read can
        # tell, and no newest-moment query was spent finding out.
        return ReadCoverage(
            status=COVERAGE_PARTIAL, reason=REASON_CALLER_LIMIT,
            requested_start=requested_start, requested_end=None,
            observed_through=observed, complete_through=None,
            freshness=ReadFreshness.UNKNOWN,
            truncated=True, item_count=item_count,
        )
    if item_count == 0 and newest is None:
        # The trustworthy empty: nothing returned, and the scope is
        # independently known to hold nothing.
        return ReadCoverage(
            status=COVERAGE_COMPLETE, reason=REASON_EMPTY_WINDOW,
            requested_start=requested_start, requested_end=None,
            observed_through=None, complete_through=None,
            freshness=ReadFreshness.UNKNOWN,
            truncated=False, item_count=0,
        )
    if newest is not None and observed is not None and newest <= observed:
        # Equality is the ordinary stable-store case: the newest moment read
        # is the newest moment the store itself records for this scope.
        return ReadCoverage(
            status=COVERAGE_COMPLETE, reason=REASON_FULL_WINDOW_OBSERVED,
            requested_start=requested_start, requested_end=None,
            observed_through=observed, complete_through=observed,
            freshness=ReadFreshness.EVIDENCE_CONSISTENT,
            truncated=False, item_count=item_count,
        )
    # The store moved under the read: a newer observation appeared in this
    # scope after the item query. Reported, not chased.
    return ReadCoverage(
        status=COVERAGE_PARTIAL, reason=REASON_TIMESTAMP_MISMATCH,
        requested_start=requested_start, requested_end=None,
        observed_through=observed, complete_through=None,
        freshness=ReadFreshness.POTENTIALLY_STALE,
        truncated=False, item_count=item_count,
    )


class StoreMessageSource:
    """The store the macOS app fills from the visual capture path.

    This is the reader this bridge has always been. The statements, the
    ordering, the clamping and the two opt-ins are unchanged; they have only
    moved behind the same interface every other source implements.

    What it now adds is a statement about each answer. Every collection read
    is followed, when its limit was not filled, by one fixed query for the
    store's newest recorded moment in exactly the scope that was read --
    ``conversations.last_seen_at`` for the conversation list, because the app
    updates that whenever a conversation is observed whether or not a message
    was stored; ``messages.first_observed_at`` for the two message reads. The
    read is complete only if it reached that moment.
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

    def list_conversations(self, limit: int) -> ReadResult[NormalizedConversation]:
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
            filled = len(rows) >= limit
            newest = None
            if not filled:
                # The conversations table's own clock, not the messages'.
                newest = connection.execute(
                    "SELECT MAX(last_seen_at) FROM conversations;"
                ).fetchone()[0]
        finally:
            connection.close()
        conversations = tuple(
            NormalizedConversation(
                id=row["id"],
                title=row["title"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
                source=self.name,
            )
            for row in rows
        )
        coverage = _conservative_coverage(
            item_count=len(conversations),
            observed=_newest(item.last_seen_at for item in conversations),
            filled=filled,
            newest=newest,
        )
        return ReadResult(items=conversations, coverage=coverage)

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
    ) -> ReadResult[NormalizedMessage]:
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
            filled = len(rows) >= limit
            newest = None
            if not filled:
                # The same scope as the read, cursor included: a cursor is a
                # page boundary, and what lies past it is not this page's.
                if before_sequence is None:
                    newest = connection.execute(
                        "SELECT MAX(first_observed_at) FROM messages "
                        "WHERE conversation_id = ?;",
                        (conversation_id,),
                    ).fetchone()[0]
                else:
                    newest = connection.execute(
                        "SELECT MAX(first_observed_at) FROM messages "
                        "WHERE conversation_id = ? AND sequence < ?;",
                        (conversation_id, int(before_sequence)),
                    ).fetchone()[0]
        finally:
            connection.close()
        messages = tuple(self._message(row) for row in reversed(rows))
        coverage = _conservative_coverage(
            item_count=len(messages),
            observed=_newest(item.first_observed_at for item in messages),
            filled=filled,
            newest=newest,
        )
        return ReadResult(items=messages, coverage=coverage)

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> ReadResult[NormalizedMessage]:
        since = float(since_observed_at)
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
                (since, limit),
            ).fetchall()
            filled = len(rows) >= limit
            newest = None
            if not filled:
                # Bounded below exactly as the read was; rows older than the
                # caller's bound are not in this scope at all.
                newest = connection.execute(
                    "SELECT MAX(first_observed_at) FROM messages "
                    "WHERE first_observed_at >= ?;",
                    (since,),
                ).fetchone()[0]
        finally:
            connection.close()
        messages = tuple(self._message(row) for row in rows)
        coverage = _conservative_coverage(
            item_count=len(messages),
            observed=_newest(item.first_observed_at for item in messages),
            filled=filled,
            newest=newest,
            requested_start=since,
        )
        return ReadResult(items=messages, coverage=coverage)


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

