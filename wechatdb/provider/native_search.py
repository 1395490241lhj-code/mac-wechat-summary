"""Native search as a candidate index. It proposes rows; it never states them.

A native search index, where one exists, is asked a bounded question and may
answer with *coordinates*: which ordinary part, which conversation table inside
it, which local id. That is the whole of what it may say. Each coordinate is
then re-read from the ordinary shard through the ordinary parser, and only a row
that is really there is projected, by ProviderResult.message.

So the ownership does not move. Identity, content, sender, chronology and
coverage stay exactly where they were, because the index never reaches the
answer -- it only nominates a row for an authoritative read. A hit that names a
row the shard does not hold, a part the caller never supplied, a conversation
the part does not carry, or a generation this provider cannot read, is dropped
and counted. Nothing is fuzzy-matched: the three coordinates are exact, and a
candidate that does not match exactly is not a weaker match, it is nothing.

The reverse direction is refused too, and this is the half that matters most.
An absent index, an unsupported schema, a malformed database, a query that
fails, and a query that matches nothing all produce this module's own result
type, which carries a capability state and a fixed counter vocabulary and no
ReadCoverage at all. An index that finds nothing says only that it found
nothing. It cannot strengthen coverage, weaken it, or turn a complete ordinary
read into a partial one, because ReadCoverage has exactly one producer and this
module is not it.

Nothing here finds a database. The index and the ordinary parts are supplied as
entries, opened read-only through the shared opener, and no directory is ever
listed, widened, or searched. Every statement is a literal with bound
parameters, so no caller string and no caller-supplied identifier can reach SQL
construction, and every failure is answered with a fixed, content-free state
rather than with SQLite's own text.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import wechatdb
from wechatdb.parser import CONVERSATION_TABLE

from .compatibility import UnsupportedGeneration, require_supported_surface
from .discovery import ReadOnlySqliteOpener, ShardEntry
from .result import ProviderResult

try:
    from message_source import NormalizedMessage
except ImportError:  # pragma: no cover - imported from another cwd
    import os
    import sys

    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "bridge",
        ),
    )
    from message_source import NormalizedMessage


# -- capability, closed vocabulary -----------------------------------------------

CAPABILITY_AVAILABLE = "available"
CAPABILITY_ABSENT = "absent"
CAPABILITY_UNSUPPORTED = "unsupported"
CAPABILITY_MALFORMED = "malformed"

CAPABILITY_STATES = frozenset({
    CAPABILITY_AVAILABLE,
    CAPABILITY_ABSENT,
    CAPABILITY_UNSUPPORTED,
    CAPABILITY_MALFORMED,
})

#: The one relation this module recognises as the index's content table. Its
#: name, and the coordinates below, are this module's own answer to "what is the
#: least a search index must expose to be addressable at all?" -- not a claim
#: about any real generation's schema.
SEARCH_CONTENT_TABLE = "search_index_content"

#: Coordinates without which a hit names nothing: the ordinary part, the
#: conversation table inside it, and the row inside that.
LINKAGE_COLUMNS: tuple[str, ...] = ("shard_name", "conversation_table", "local_id")

#: One term, bounded. Long enough for a word, short enough that the query is
#: always cheaper than the authoritative reads it causes.
MAX_TERM_LENGTH = 128
MAX_RESULT_LIMIT = 200

_SCHEMA_SQL = "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?"
#: ``local_id`` is read as it is stored, never CAST: a coercion would turn a
#: coordinate the index could not state into ``0``, which is a real row's id.
_CANDIDATE_SQL = (
    'SELECT "shard_name", "conversation_table", "local_id" FROM "'
    + SEARCH_CONTENT_TABLE + '" WHERE "' + SEARCH_CONTENT_TABLE
    + '" MATCH ? ORDER BY rowid LIMIT ?'
)
_COLUMN_SQL = "PRAGMA table_info('" + SEARCH_CONTENT_TABLE + "')"


# -- what the index may say ------------------------------------------------------

@dataclass(frozen=True, slots=True)
class NativeSearchDiagnostics:
    """Fixed counters, and nothing that could identify anything.

    No path, no schema, no table, no SQL, no exception text, no content and no
    identifier. Deliberately *not* a ReadCoverage: an index that found nothing
    has learned nothing about what messages exist.
    """

    candidates: int = 0
    resolved: int = 0
    dropped: int = 0
    duplicates: int = 0
    query_failed: int = 0

    def __post_init__(self) -> None:
        for value in (self.candidates, self.resolved, self.dropped,
                      self.duplicates, self.query_failed):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("a diagnostic count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class NativeSearchResult:
    """Authoritative messages, and what the index contributed to finding them.

    The messages are the ordinary path's own NormalizedMessage values, so their
    identity, content and chronology were decided by the ordinary reader.
    used_native_index records only that the index nominated them. truncated is
    the bounded query's own statement that more candidates existed than this
    result holds; there is no completeness field here for anyone to misread.
    """

    capability: str
    messages: tuple[NormalizedMessage, ...]
    used_native_index: bool
    truncated: bool
    diagnostics: NativeSearchDiagnostics

    def __post_init__(self) -> None:
        if self.capability not in CAPABILITY_STATES:
            raise ValueError("capability is not in the closed vocabulary")
        if not isinstance(self.messages, tuple):
            raise ValueError("search messages must be a tuple")
        if not isinstance(self.used_native_index, bool):
            raise ValueError("used_native_index must be a boolean")
        if not isinstance(self.truncated, bool):
            raise ValueError("truncated must be a boolean")
        if not isinstance(self.diagnostics, NativeSearchDiagnostics):
            raise ValueError("search diagnostics are never coverage")


def _term(query: object) -> str:
    """One bounded term, or a refusal.

    Binding the parameter already prevents injection, so this is not about
    safety; it is about refusing to hand a caller the index's query language. An
    operator, a column filter, a quoted phrase and a wildcard are all ways of
    asking something other than "these words", and this spike answers only that
    last question.
    """
    if not isinstance(query, str) or len(query) > MAX_TERM_LENGTH:
        raise ValueError("search query must be one bounded term")
    if not query.isalnum():
        raise ValueError("search query must be one bounded term")
    return query


def _limit(limit: object) -> int:
    if (isinstance(limit, bool) or not isinstance(limit, int)
            or not 1 <= limit <= MAX_RESULT_LIMIT):
        raise ValueError("search limit must be a positive bounded integer")
    return limit


def _coordinates(row):
    """One hit's coordinates, or None when it addresses nothing.

    The index's own row id is deliberately not among them: it is a token inside
    the index, and a token is not a reference to a message.
    """
    if not (len(row) == 3
            and isinstance(row[0], str) and row[0]
            and isinstance(row[1], str)
            and CONVERSATION_TABLE.fullmatch(row[1]) is not None
            and isinstance(row[2], int) and not isinstance(row[2], bool)
            and row[2] >= 0):
        return None
    return row


def _order(message: NormalizedMessage) -> tuple[int, int]:
    return (message.sequence, message.conversation_id)


def _compatible(connection: sqlite3.Connection) -> bool:
    """Whether this database carries an index this module knows how to ask.

    Three things must hold: the content relation exists, it carries every
    coordinate column, and it is a full-text relation rather than an ordinary
    table wearing the same name. An ordinary table would accept the query and
    answer with silence, and this module will not claim a capability on the
    strength of a matching name.
    """
    row = connection.execute(_SCHEMA_SQL, (SEARCH_CONTENT_TABLE,)).fetchone()
    if row is None or not isinstance(row[0], str):
        return False
    statement = row[0].upper()
    if "VIRTUAL TABLE" not in statement or "FTS5" not in statement:
        return False
    present = {entry[1] for entry in connection.execute(_COLUMN_SQL)}
    return set(LINKAGE_COLUMNS) <= present


def _without_index(capability: str, *, query_failed: bool = False) -> NativeSearchResult:
    return NativeSearchResult(
        capability=capability,
        messages=(),
        used_native_index=False,
        truncated=False,
        diagnostics=NativeSearchDiagnostics(query_failed=int(query_failed)),
    )


# -- the index --------------------------------------------------------------------

class NativeSearchIndex:
    """One optional index beside a set of ordinary parts, wired to nothing.

    Both are supplied. Nothing is found, listed, or widened, and a part the
    caller did not name is never opened even to see what it holds.
    """

    def __init__(self, index: ShardEntry | None, *, entries, identities) -> None:
        self._index = index
        self._parts: dict[str, ShardEntry] = {}
        for entry in entries:
            if entry.name in self._parts:
                raise ValueError("two parts share one name")
            self._parts[entry.name] = entry
        # Bound once, by name, exactly as the sharded provider does it.
        self._names = getattr(identities, "resolve")
        self._opener = ReadOnlySqliteOpener()

    def search(self, query: str, limit: int) -> NativeSearchResult:
        """Bounded candidates, bounded authoritative reads, one honest answer."""
        term = _term(query)
        limit = _limit(limit)
        if self._index is None:
            return _without_index(CAPABILITY_ABSENT)

        try:
            connection = self._opener.open(self._index)
        except sqlite3.Error:
            return _without_index(CAPABILITY_MALFORMED, query_failed=True)
        try:
            try:
                compatible = _compatible(connection)
            except sqlite3.Error:
                # Bytes that are not a database answer no question at all, and
                # their own error text never leaves this module.
                return _without_index(CAPABILITY_MALFORMED, query_failed=True)
            if not compatible:
                return _without_index(CAPABILITY_UNSUPPORTED)
            try:
                rows = connection.execute(
                    _CANDIDATE_SQL, (term, limit + 1)).fetchall()
            except sqlite3.Error:
                return _without_index(CAPABILITY_MALFORMED, query_failed=True)
        finally:
            connection.close()

        candidates = [_coordinates(row) for row in rows]
        messages, dropped, duplicates = self._resolve(
            [candidate for candidate in candidates if candidate is not None])
        # One more candidate than the limit was asked for, so truncation is
        # measured rather than assumed.
        return NativeSearchResult(
            capability=CAPABILITY_AVAILABLE,
            messages=tuple(sorted(messages.values(), key=_order))[-limit:],
            used_native_index=True,
            truncated=len(rows) > limit,
            diagnostics=NativeSearchDiagnostics(
                candidates=len(rows), resolved=len(messages),
                dropped=dropped + candidates.count(None), duplicates=duplicates),
        )

    # -- candidate -> authoritative message ------------------------------------

    def _resolve(self, candidates):
        """One ordinary read per (part, conversation), then exact row selection.

        Deduplication happens here and only here, on the projected message's own
        identity: two candidates naming the same authoritative message are one
        message, and two *different* messages that happen to carry the same text
        are two messages.
        """
        messages: dict[int, NormalizedMessage] = {}
        cache: dict[tuple[str, str], tuple | None] = {}
        dropped = duplicates = 0
        for shard_name, table, local_id in candidates:
            entry = self._parts.get(shard_name)
            if entry is None:
                dropped += 1
                continue
            if (entry.name, table) not in cache:
                cache[(entry.name, table)] = self._read(entry, table)
            records = cache[(entry.name, table)]
            record = (next((r for r in records if r.local_id == local_id), None)
                      if records is not None else None)
            if record is None:
                dropped += 1
                continue
            message = ProviderResult.message(record)
            if message.id in messages:
                duplicates += 1
                continue
            messages[message.id] = message
        return messages, dropped, duplicates

    def _read(self, entry: ShardEntry, table: str):
        """The ordinary authoritative read, or None for anything else.

        Refusing a generation this provider does not support is the same refusal
        the ordinary read makes, for the same reason: a hit must not be able to
        reach a row the ordinary reader would itself have refused to read. The
        open is guarded for that same reason: a part that will not open is a
        dropped candidate, never an exception carrying its path.
        """
        try:
            connection = self._opener.open(entry)
        except sqlite3.Error:
            return None
        try:
            require_supported_surface(connection, table)
            name2id = wechatdb.load_name2id(connection)
            records = list(wechatdb.parse_conversation(
                connection, table, name2id=name2id,
                session_names=self._names().session_names))
            if not records:
                return ()
            # The same room-scoped resolution the ordinary provider makes, and
            # the same re-parse with it. A hit must not produce a weaker
            # projection of a row the ordinary reader shows in full.
            resolved = self._names(room=records[0].session_id)
            if resolved.display_names:
                records = list(wechatdb.parse_conversation(
                    connection, table, name2id=name2id,
                    session_names=resolved.session_names,
                    display_names=resolved.display_names))
            return tuple(records)
        except (sqlite3.Error, UnsupportedGeneration):
            return None
        finally:
            connection.close()
