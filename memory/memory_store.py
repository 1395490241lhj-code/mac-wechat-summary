"""The local memory store: canonical records, coverage, and a text index.

This is a *second* database, deliberately. It is not the app's message store
and it never opens, migrates, mutates, or repurposes it. The app's store is the
visual reader's own file, and coupling memory to it would make memory a
property of one reader — exactly what this layer exists to avoid. The memory
store is written only by :mod:`memory_ingest` and read only through
:mod:`memory_retrieval`, and both go through the consent gate first.

What it holds
-------------

* **Canonical conversations and messages**, in one representation regardless of
  which source produced them. A record knows its source and the source's own
  identifier, so it can always be traced back, but nothing about its shape
  depends on the source.
* **Ingestion runs** — when a run happened, whether it finished, what it saw.
* **Coverage** — which windows of which sources were actually observed, and
  how well. This is the reason the store can distinguish "there are no
  messages" from "nobody looked".
* **An FTS5 index** over message text and sender.

What it deliberately does not hold
----------------------------------

No image, frame, screenshot, bubble geometry, provider response, credential,
or filesystem path. The absence is structural: there is no column for any of
them, as in the app's own store. The one path in the system — the store's own
location — is supplied by the operator through the environment and is never
written into the database.

Chinese text and FTS5
---------------------

FTS5's ``unicode61`` tokeniser splits on non-word characters, and a run of Han
characters has none, so an entire Chinese sentence would become a single token
and "会议" would never match it. The index therefore stores a *segmented* copy
of the text in which every ideograph is its own token, and a query is segmented
the same way and issued as a phrase. "会议" becomes the phrase ``"会 议"``,
which matches the adjacent pair and nothing else — exact substring matching for
Chinese, with Latin words left as ordinary words. The stored ``text`` column
always keeps the original, unsegmented string; the segmented form exists only
inside the index.

The ``trigram`` tokeniser was the alternative. It was not chosen because it
cannot match a query shorter than three characters, and a great many Chinese
words are two.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

from memory_consent import ConsentDecision, MemoryConsentError

#: Published through SQLite's ``user_version``, the same contract the app store
#: and the bridge use. An unrecognised version fails closed; the shape is never
#: inferred from whichever tables happen to exist.
MEMORY_SCHEMA_VERSION: int = 1

# --- Coverage vocabulary -----------------------------------------------------
#
# The four states a question about a window can be in. They are not degrees of
# the same thing: "observed and empty" is knowledge, "not observed" is the
# absence of knowledge, and turning the second into the first is the specific
# failure this vocabulary exists to prevent.

#: The source was read and the whole window was covered.
COVERAGE_COMPLETE: str = "observed_complete"

#: The source was read but the window was not covered in full -- a limit was
#: reached, paging was refused, the read was cut short.
COVERAGE_PARTIAL: str = "observed_partial"

#: The source was asked and could not answer at all.
COVERAGE_UNAVAILABLE: str = "unavailable"

#: Nothing has ever been recorded for this window. Never stored as a row: it is
#: what the absence of a row means.
COVERAGE_NOT_OBSERVED: str = "not_observed"

COVERAGE_STATES: frozenset[str] = frozenset(
    {COVERAGE_COMPLETE, COVERAGE_PARTIAL, COVERAGE_UNAVAILABLE}
)

# --- Timestamp meaning -------------------------------------------------------
#
# One column, two meanings, and the difference matters enough to be stored
# beside the value rather than inferred from the source name later.

#: When this Mac first saw the message on screen (the visual path).
TIME_FIRST_OBSERVED: str = "first_observed"

#: When the message was created according to the source itself (a database).
TIME_SOURCE_CREATED: str = "source_created"

#: A source that reports a timestamp without saying what it means.
TIME_SOURCE_REPORTED: str = "source_reported"

TIMESTAMP_KINDS: frozenset[str] = frozenset(
    {TIME_FIRST_OBSERVED, TIME_SOURCE_CREATED, TIME_SOURCE_REPORTED}
)

RUN_RUNNING: str = "running"
RUN_SUCCEEDED: str = "succeeded"
RUN_FAILED: str = "failed"

_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS conversations (
        canonical_id            TEXT PRIMARY KEY,
        source                  TEXT NOT NULL,
        source_conversation_id  TEXT NOT NULL,
        display_name            TEXT,
        kind                    TEXT,
        first_seen_at           REAL,
        last_seen_at            REAL,
        first_ingested_at       REAL NOT NULL,
        last_observed_at        REAL NOT NULL,
        UNIQUE (source, source_conversation_id)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS messages (
        canonical_id              TEXT PRIMARY KEY,
        source                    TEXT NOT NULL,
        source_message_id         TEXT,
        conversation_canonical_id TEXT NOT NULL
            REFERENCES conversations (canonical_id),
        identity_mode             TEXT NOT NULL,
        content_fingerprint       TEXT NOT NULL,
        sender                    TEXT,
        ownership                 TEXT NOT NULL,
        sequence                  INTEGER,
        timestamp                 REAL NOT NULL,
        timestamp_kind            TEXT NOT NULL,
        visible_time              TEXT,
        kind                      TEXT NOT NULL,
        text                      TEXT,
        confidence                REAL,
        reply_to_canonical_id     TEXT,
        first_ingested_at         REAL NOT NULL,
        last_observed_at          REAL NOT NULL,
        observation_count         INTEGER NOT NULL,
        first_run_id              TEXT NOT NULL,
        last_run_id               TEXT NOT NULL
    );
    """,
    # A source id, when there is one, is unique within its source. Messages
    # without one (derived identity) are excluded from the constraint rather
    # than all colliding on NULL.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS messages_source_identity
        ON messages (source, source_message_id)
        WHERE source_message_id IS NOT NULL;
    """,
    """
    CREATE INDEX IF NOT EXISTS messages_by_conversation
        ON messages (conversation_canonical_id, timestamp);
    """,
    "CREATE INDEX IF NOT EXISTS messages_by_time ON messages (timestamp);",
    """
    CREATE INDEX IF NOT EXISTS messages_by_fingerprint
        ON messages (content_fingerprint);
    """,
    """
    CREATE TABLE IF NOT EXISTS ingestion_runs (
        run_id              TEXT PRIMARY KEY,
        source              TEXT NOT NULL,
        started_at          REAL NOT NULL,
        completed_at        REAL,
        state               TEXT NOT NULL,
        failure_state       TEXT,
        conversations_seen  INTEGER NOT NULL DEFAULT 0,
        messages_seen       INTEGER NOT NULL DEFAULT 0,
        messages_inserted   INTEGER NOT NULL DEFAULT 0,
        messages_updated    INTEGER NOT NULL DEFAULT 0,
        duplicates_detected INTEGER NOT NULL DEFAULT 0
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS coverage (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id                    TEXT NOT NULL REFERENCES ingestion_runs (run_id),
        source                    TEXT NOT NULL,
        conversation_canonical_id TEXT,
        window_start              REAL,
        window_end                REAL,
        status                    TEXT NOT NULL,
        reason                    TEXT,
        message_count             INTEGER NOT NULL DEFAULT 0,
        recorded_at               REAL NOT NULL
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS coverage_by_scope
        ON coverage (source, conversation_canonical_id, recorded_at);
    """,
    # Contentless FTS5: the index holds terms and positions, never a second
    # readable copy of the message. `contentless_delete` (SQLite 3.43+) is what
    # makes a re-indexed message removable; without it a corrected message
    # would leave its old terms searchable forever.
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5 (
        segmented_text,
        sender,
        content = '',
        contentless_delete = 1,
        tokenize = 'unicode61'
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS messages_fts_map (
        rowid        INTEGER PRIMARY KEY,
        canonical_id TEXT NOT NULL UNIQUE
    );
    """,
)

# Codepoint ranges whose characters are indexed one token per character.
# Han, Hiragana, Katakana, Hangul syllables, CJK compatibility and the
# supplementary ideographic plane.
_PER_CHARACTER_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x2FFF),
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xA000, 0xA4CF),
    (0xAC00, 0xD7AF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2FA1F),
)


class MemoryStoreError(Exception):
    """The store cannot do what was asked, and will not approximate it.

    Like every other refusal in this project, ``state`` is a fixed token and
    ``detail`` a fixed sentence: no chat content, no sender, no path.
    """

    def __init__(self, state: str, detail: str) -> None:
        super().__init__(detail)
        self.state = state
        self.detail = detail


def is_per_character(character: str) -> bool:
    codepoint = ord(character)
    return any(low <= codepoint <= high for low, high in _PER_CHARACTER_RANGES)


def segment(text: str) -> str:
    """Spaces every ideograph so ``unicode61`` makes one token per character.

    Latin, digits and punctuation are untouched, so English keeps word
    tokenisation. This is applied identically to indexed text and to a query,
    which is the only reason the two can match.
    """
    pieces: list[str] = []
    for character in text:
        if is_per_character(character):
            pieces.append(" ")
            pieces.append(character)
            pieces.append(" ")
        else:
            pieces.append(character)
    return " ".join("".join(pieces).split())


def match_expression(query: str) -> str:
    """Turns free text into a safe FTS5 MATCH expression.

    Every whitespace-separated term becomes a quoted phrase, so no character a
    user can type is ever interpreted as FTS5 syntax, and a Chinese term
    becomes a phrase over its own characters. Terms are ANDed, which is FTS5's
    default between phrases.
    """
    phrases: list[str] = []
    for term in query.split():
        segmented = segment(term)
        if not segmented:
            continue
        phrases.append('"' + segmented.replace('"', '""') + '"')
    if not phrases:
        raise MemoryStoreError(
            "query_empty", "The search query contains nothing searchable."
        )
    return " ".join(phrases)


@dataclass(frozen=True)
class CoverageRecord:
    """One statement about what was actually observed."""

    source: str
    status: str
    conversation_canonical_id: str | None = None
    window_start: float | None = None
    window_end: float | None = None
    reason: str | None = None
    message_count: int = 0

    def __post_init__(self) -> None:
        if self.status not in COVERAGE_STATES:
            raise MemoryStoreError(
                "coverage_status_unknown",
                "A coverage record was given an unrecognised status.",
            )


@dataclass(frozen=True)
class CoverageVerdict:
    """What the store knows about one question's window.

    ``status`` is one of the four coverage states, including
    :data:`COVERAGE_NOT_OBSERVED`, which no row carries and which therefore can
    only be produced here. A caller that reports "no messages" without reading
    this field is making a claim the store did not make.
    """

    status: str
    source: str | None
    reasons: tuple[str, ...] = ()
    observed_at: float | None = None

    @property
    def is_complete(self) -> bool:
        return self.status == COVERAGE_COMPLETE


@dataclass
class MemoryStore:
    """An open, consented memory database.

    Instances are created through :meth:`open`, never by opening a path
    directly: the consent gate is the constructor.
    """

    connection: sqlite3.Connection
    path: str
    _closed: bool = field(default=False, init=False)

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    def open(cls, decision: ConsentDecision) -> "MemoryStore":
        """Opens (creating if needed) the store the consent decision allows.

        The decision is the only way to name a file. A refusal raises
        :class:`memory_consent.MemoryConsentError` from ``require()`` and never
        reaches SQLite.
        """
        path = decision.require()
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute("PRAGMA journal_mode = WAL;")
        connection.execute("PRAGMA busy_timeout = 5000;")
        store = cls(connection=connection, path=path)
        try:
            store.migrate()
        except Exception:
            connection.close()
            raise
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - filesystem without chmod
            pass
        return store

    def migrate(self) -> int:
        """Creates the schema, or accepts an existing recognised one.

        Version 0 means an empty file and is migrated to the current version.
        The current version is accepted as-is. Anything else fails closed
        rather than guessing what the shape might be -- the same rule the
        bridge applies to the app's store.
        """
        version = int(self.connection.execute("PRAGMA user_version;").fetchone()[0])
        if version == MEMORY_SCHEMA_VERSION:
            return version
        if version != 0:
            raise MemoryStoreError(
                "schema_unsupported",
                "The memory database was written by a different schema version.",
            )
        try:
            with self.transaction():
                for statement in _SCHEMA:
                    self.connection.execute(statement)
                self.connection.execute(
                    f"PRAGMA user_version = {MEMORY_SCHEMA_VERSION};"
                )
        except sqlite3.OperationalError as error:
            # The one build-dependent requirement in this file. Say so plainly
            # instead of creating a store whose index cannot forget anything.
            raise MemoryStoreError(
                "fts_unsupported",
                "This SQLite build cannot create the required full-text index "
                "(FTS5 with contentless_delete, SQLite 3.43 or newer).",
            ) from error
        return MEMORY_SCHEMA_VERSION

    @property
    def schema_version(self) -> int:
        return int(self.connection.execute("PRAGMA user_version;").fetchone()[0])

    def close(self) -> None:
        if not self._closed:
            self.connection.close()
            self._closed = True

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    class _Transaction:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def __enter__(self) -> sqlite3.Connection:
            self._connection.execute("BEGIN IMMEDIATE;")
            return self._connection

        def __exit__(self, exc_type, *_: object) -> bool:
            if exc_type is None:
                self._connection.execute("COMMIT;")
            else:
                self._connection.execute("ROLLBACK;")
            return False

    def transaction(self) -> "MemoryStore._Transaction":
        """One run, one transaction. A failed run leaves nothing behind."""
        return MemoryStore._Transaction(self.connection)

    # -- writes (used by the ingestor; not a public ingestion API) ------------

    def upsert_conversation(
        self,
        *,
        canonical_id: str,
        source: str,
        source_conversation_id: str,
        display_name: str | None,
        kind: str | None,
        first_seen_at: float | None,
        last_seen_at: float | None,
        now: float,
    ) -> None:
        """Records or refreshes one conversation.

        ``first_ingested_at`` is written once and never moved. Everything else
        is refreshed, but only *forward*: a re-read that reports an older
        ``last_seen_at`` does not walk the record backwards, and a re-read that
        reports no title does not erase one we already have.
        """
        self.connection.execute(
            """
            INSERT INTO conversations (
                canonical_id, source, source_conversation_id, display_name,
                kind, first_seen_at, last_seen_at, first_ingested_at,
                last_observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (canonical_id) DO UPDATE SET
                display_name = COALESCE(excluded.display_name, display_name),
                kind = COALESCE(excluded.kind, kind),
                first_seen_at = MIN(
                    COALESCE(excluded.first_seen_at, first_seen_at),
                    COALESCE(first_seen_at, excluded.first_seen_at)
                ),
                last_seen_at = MAX(
                    COALESCE(excluded.last_seen_at, last_seen_at),
                    COALESCE(last_seen_at, excluded.last_seen_at)
                ),
                last_observed_at = excluded.last_observed_at;
            """,
            (
                canonical_id,
                source,
                source_conversation_id,
                display_name,
                kind,
                first_seen_at,
                last_seen_at,
                now,
                now,
            ),
        )

    def message_exists(self, canonical_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM messages WHERE canonical_id = ?;", (canonical_id,)
        ).fetchone()
        return row is not None

    def fingerprint_holders(self, fingerprint: str) -> list[str]:
        """Which canonical ids already carry this fingerprint."""
        return [
            row["canonical_id"]
            for row in self.connection.execute(
                "SELECT canonical_id FROM messages WHERE content_fingerprint = ?;",
                (fingerprint,),
            )
        ]

    def upsert_message(self, values: dict[str, Any]) -> str:
        """Writes one message and returns ``"inserted"`` or ``"updated"``.

        A repeat observation of a known message updates observation metadata --
        ``last_observed_at``, ``observation_count``, ``last_run_id`` -- and
        fills in fields that were previously unknown. It never rewrites text
        that is already there with ``NULL``, and it never moves
        ``first_ingested_at``, because when we first wrote a message down is a
        fact about us and does not change.
        """
        existing = self.connection.execute(
            "SELECT rowid, observation_count FROM messages WHERE canonical_id = ?;",
            (values["canonical_id"],),
        ).fetchone()
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO messages (
                    canonical_id, source, source_message_id,
                    conversation_canonical_id, identity_mode, content_fingerprint,
                    sender, ownership, sequence, timestamp, timestamp_kind,
                    visible_time, kind, text, confidence, reply_to_canonical_id,
                    first_ingested_at, last_observed_at, observation_count,
                    first_run_id, last_run_id
                ) VALUES (
                    :canonical_id, :source, :source_message_id,
                    :conversation_canonical_id, :identity_mode, :content_fingerprint,
                    :sender, :ownership, :sequence, :timestamp, :timestamp_kind,
                    :visible_time, :kind, :text, :confidence, :reply_to_canonical_id,
                    :now, :now, 1, :run_id, :run_id
                );
                """,
                values,
            )
            rowid = int(
                self.connection.execute(
                    "SELECT rowid FROM messages WHERE canonical_id = ?;",
                    (values["canonical_id"],),
                ).fetchone()["rowid"]
            )
            self._index_message(rowid, values["canonical_id"], values["text"], values["sender"])
            return "inserted"
        self.connection.execute(
            """
            UPDATE messages SET
                sender = COALESCE(:sender, sender),
                sequence = COALESCE(:sequence, sequence),
                visible_time = COALESCE(:visible_time, visible_time),
                text = COALESCE(:text, text),
                confidence = COALESCE(:confidence, confidence),
                reply_to_canonical_id =
                    COALESCE(:reply_to_canonical_id, reply_to_canonical_id),
                content_fingerprint = :content_fingerprint,
                last_observed_at = :now,
                observation_count = observation_count + 1,
                last_run_id = :run_id
            WHERE canonical_id = :canonical_id;
            """,
            values,
        )
        row = self.connection.execute(
            "SELECT rowid, text, sender FROM messages WHERE canonical_id = ?;",
            (values["canonical_id"],),
        ).fetchone()
        self._index_message(
            int(row["rowid"]), values["canonical_id"], row["text"], row["sender"]
        )
        return "updated"

    def _index_message(
        self, rowid: int, canonical_id: str, text: str | None, sender: str | None
    ) -> None:
        """Keeps the FTS index in step with one message row.

        The index is contentless, so an update is a delete followed by an
        insert at the same rowid; the map table is what makes a hit resolvable
        back to a canonical id without the index holding one.
        """
        self.connection.execute("DELETE FROM messages_fts WHERE rowid = ?;", (rowid,))
        self.connection.execute(
            "INSERT INTO messages_fts (rowid, segmented_text, sender) VALUES (?, ?, ?);",
            (rowid, segment(text or ""), segment(sender or "")),
        )
        self.connection.execute(
            "INSERT INTO messages_fts_map (rowid, canonical_id) VALUES (?, ?)"
            " ON CONFLICT (rowid) DO UPDATE SET canonical_id = excluded.canonical_id;",
            (rowid, canonical_id),
        )

    # -- runs and coverage ---------------------------------------------------

    def begin_run(self, run_id: str, source: str, started_at: float) -> None:
        self.connection.execute(
            "INSERT INTO ingestion_runs (run_id, source, started_at, state)"
            " VALUES (?, ?, ?, ?);",
            (run_id, source, started_at, RUN_RUNNING),
        )

    def finish_run(
        self,
        run_id: str,
        *,
        state: str,
        completed_at: float,
        failure_state: str | None = None,
        conversations_seen: int = 0,
        messages_seen: int = 0,
        messages_inserted: int = 0,
        messages_updated: int = 0,
        duplicates_detected: int = 0,
    ) -> None:
        self.connection.execute(
            """
            UPDATE ingestion_runs SET
                state = ?, completed_at = ?, failure_state = ?,
                conversations_seen = ?, messages_seen = ?, messages_inserted = ?,
                messages_updated = ?, duplicates_detected = ?
            WHERE run_id = ?;
            """,
            (
                state,
                completed_at,
                failure_state,
                conversations_seen,
                messages_seen,
                messages_inserted,
                messages_updated,
                duplicates_detected,
                run_id,
            ),
        )

    def record_coverage(
        self, run_id: str, record: CoverageRecord, recorded_at: float
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO coverage (
                run_id, source, conversation_canonical_id, window_start,
                window_end, status, reason, message_count, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                run_id,
                record.source,
                record.conversation_canonical_id,
                record.window_start,
                record.window_end,
                record.status,
                record.reason,
                record.message_count,
                recorded_at,
            ),
        )

    def assess_coverage(
        self,
        *,
        source: str | None = None,
        conversation_canonical_id: str | None = None,
        start: float | None = None,
        end: float | None = None,
    ) -> CoverageVerdict:
        """What the store can honestly claim about one window.

        A window is *covered* only by a record whose own window contains it: a
        record with an open end covers any later time, and a record bounded
        before the question's end does not. Records are read newest first, and
        the first that speaks to the window decides, so a later ``unavailable``
        correctly overrides an earlier success.

        With no record at all the answer is :data:`COVERAGE_NOT_OBSERVED`. That
        is the whole point of this method: "we have nothing" and "we looked and
        there was nothing" are different answers and the caller must be able to
        tell them apart.
        """
        clauses: list[str] = []
        parameters: list[Any] = []
        if source is not None:
            clauses.append("source = ?")
            parameters.append(source)
        if conversation_canonical_id is not None:
            # A source-wide record (NULL conversation) speaks for every
            # conversation in that source, so it stays in scope.
            clauses.append(
                "(conversation_canonical_id IS NULL OR conversation_canonical_id = ?)"
            )
            parameters.append(conversation_canonical_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.connection.execute(
            "SELECT source, conversation_canonical_id, window_start, window_end,"
            " status, reason, recorded_at FROM coverage"
            f"{where} ORDER BY recorded_at DESC, id DESC;",
            parameters,
        ).fetchall()
        partial_reasons: list[str] = []
        for row in rows:
            if not _window_contains(
                row["window_start"], row["window_end"], start, end
            ):
                if row["status"] != COVERAGE_UNAVAILABLE and _windows_overlap(
                    row["window_start"], row["window_end"], start, end
                ):
                    partial_reasons.append(row["reason"] or "window_partially_observed")
                continue
            if row["status"] == COVERAGE_COMPLETE and partial_reasons:
                return CoverageVerdict(
                    status=COVERAGE_PARTIAL,
                    source=row["source"],
                    reasons=tuple(dict.fromkeys(partial_reasons)),
                    observed_at=row["recorded_at"],
                )
            return CoverageVerdict(
                status=row["status"],
                source=row["source"],
                reasons=(row["reason"],) if row["reason"] else (),
                observed_at=row["recorded_at"],
            )
        if partial_reasons:
            return CoverageVerdict(
                status=COVERAGE_PARTIAL,
                source=source,
                reasons=tuple(dict.fromkeys(partial_reasons)),
            )
        return CoverageVerdict(status=COVERAGE_NOT_OBSERVED, source=source)

    # -- small reads used by tests, retrieval and evidence -------------------

    def counts(self) -> dict[str, int]:
        return {
            "conversations": int(
                self.connection.execute(
                    "SELECT COUNT(*) AS n FROM conversations;"
                ).fetchone()["n"]
            ),
            "messages": int(
                self.connection.execute(
                    "SELECT COUNT(*) AS n FROM messages;"
                ).fetchone()["n"]
            ),
            "runs": int(
                self.connection.execute(
                    "SELECT COUNT(*) AS n FROM ingestion_runs;"
                ).fetchone()["n"]
            ),
            "coverage_records": int(
                self.connection.execute(
                    "SELECT COUNT(*) AS n FROM coverage;"
                ).fetchone()["n"]
            ),
        }

    def message(self, canonical_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM messages WHERE canonical_id = ?;", (canonical_id,)
        ).fetchone()

    def conversation(self, canonical_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM conversations WHERE canonical_id = ?;", (canonical_id,)
        ).fetchone()

    def runs(self) -> list[sqlite3.Row]:
        return list(
            self.connection.execute(
                "SELECT * FROM ingestion_runs ORDER BY started_at ASC, run_id ASC;"
            )
        )


def _window_contains(
    record_start: float | None,
    record_end: float | None,
    query_start: float | None,
    query_end: float | None,
) -> bool:
    """Does an observed window fully contain the asked-about window?

    ``None`` is unbounded on both sides. An unbounded record covers everything;
    an unbounded question is covered only by an unbounded record.
    """
    if record_start is not None:
        if query_start is None or query_start < record_start:
            return False
    if record_end is not None:
        if query_end is None or query_end > record_end:
            return False
    return True


def _windows_overlap(
    record_start: float | None,
    record_end: float | None,
    query_start: float | None,
    query_end: float | None,
) -> bool:
    if record_end is not None and query_start is not None and record_end < query_start:
        return False
    if record_start is not None and query_end is not None and record_start > query_end:
        return False
    return True


def new_run_id(now: float | None = None) -> str:
    """A run identifier that sorts by time and carries nothing else.

    No hostname, no username, no path, no pid: a run id ends up in evidence
    documents and must be safe to paste there.
    """
    import secrets

    moment = time.time() if now is None else now
    return f"run-{int(moment * 1000):013d}-{secrets.token_hex(4)}"


def rows_as_dicts(rows: Iterable[sqlite3.Row]) -> Iterator[dict[str, Any]]:
    for row in rows:
        yield dict(row)


__all__ = [
    "COVERAGE_COMPLETE",
    "COVERAGE_NOT_OBSERVED",
    "COVERAGE_PARTIAL",
    "COVERAGE_STATES",
    "COVERAGE_UNAVAILABLE",
    "MEMORY_SCHEMA_VERSION",
    "RUN_FAILED",
    "RUN_RUNNING",
    "RUN_SUCCEEDED",
    "TIMESTAMP_KINDS",
    "TIME_FIRST_OBSERVED",
    "TIME_SOURCE_CREATED",
    "TIME_SOURCE_REPORTED",
    "CoverageRecord",
    "CoverageVerdict",
    "MemoryConsentError",
    "MemoryStore",
    "MemoryStoreError",
    "match_expression",
    "new_run_id",
    "rows_as_dicts",
    "segment",
]
