"""Turning normalised messages into canonical memory, repeatably.

The ingestor sits between a reader and the store. It takes
:class:`message_source.NormalizedMessage` values -- the shape *both* current
readers already produce -- and writes canonical records. It knows nothing about
SQLCipher, screen capture, Gemini, or any reader's wire format, and it must
stay that way: the moment ingestion learns which reader it is talking to, the
memory layer stops being source-neutral.

Two properties are load-bearing.

**Idempotent.** Ingesting the same messages twice produces the same records.
The second pass updates observation metadata -- when we last saw the message,
how many times, which run -- and inserts nothing new. Overlapping windows are
therefore free, which matters because overlapping is the normal case: the
visual path re-reads the same screen, and a database read asks for "since T"
with a T that deliberately overlaps the last read.

**Atomic.** One run is one transaction. Every message is validated before
anything is written, so a batch containing one malformed record writes nothing
at all and the run is recorded as failed. A half-ingested batch would leave
coverage claiming a window that the messages do not actually fill.

Coverage is written by the same call that writes the messages, because a run
that records messages without recording what it looked at has produced data
nobody can safely interpret.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from memory_identity import (
    IDENTITY_DERIVED,
    IDENTITY_MODES,
    IDENTITY_SOURCE,
    content_fingerprint,
    conversation_canonical_id,
    message_canonical_id_derived,
    message_canonical_id_from_source,
)
from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    COVERAGE_UNAVAILABLE,
    RUN_FAILED,
    RUN_SUCCEEDED,
    TIME_FIRST_OBSERVED,
    TIME_SOURCE_CREATED,
    TIME_SOURCE_REPORTED,
    CoverageRecord,
    MemoryStore,
    MemoryStoreError,
    new_run_id,
)
try:
    from message_source import (
        SOURCE_DATABASE,
        SOURCE_VISUAL,
        MessageSource,
        MessageSourceError,
        NormalizedConversation,
        NormalizedMessage,
    )
except ImportError:  # pragma: no cover - imported from another cwd
    # The reader boundary lives in ``bridge/`` and is imported, never copied:
    # a second definition of the normalised shape would be the first step back
    # towards a per-reader memory model.
    import os
    import sys

    sys.path.insert(
        0,
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bridge"
        ),
    )
    from message_source import (
        SOURCE_DATABASE,
        SOURCE_VISUAL,
        MessageSource,
        MessageSourceError,
        NormalizedConversation,
        NormalizedMessage,
    )

#: What a source's identifiers are worth, per source. A source that is not
#: listed is treated as having no stable identifiers, which is the safe
#: assumption: deriving an id for a source that actually had one costs a
#: re-identification, while trusting an unstable id silently mixes records.
DEFAULT_IDENTITY_MODES: dict[str, str] = {
    # The app's store assigns each reconciled message a rowid it never reuses
    # or renumbers, and the reconciler is what stops a re-observed message
    # becoming a second row.
    SOURCE_VISUAL: IDENTITY_SOURCE,
    # WeChat's own local message id, surfaced by the reader.
    SOURCE_DATABASE: IDENTITY_SOURCE,
}

#: What ``NormalizedMessage.first_observed_at`` means, per source. The field is
#: one field with two meanings (see ``bridge/message_source.py``); the store
#: records which one, so a later reader is not left guessing.
DEFAULT_TIMESTAMP_KINDS: dict[str, str] = {
    SOURCE_VISUAL: TIME_FIRST_OBSERVED,
    SOURCE_DATABASE: TIME_SOURCE_CREATED,
}

#: A recent-message sweep of an external reader visits a bounded number of
#: conversations, so its result is partial by construction. The reason token is
#: fixed vocabulary, never free text.
REASON_LIMIT_REACHED: str = "limit_reached"
REASON_SOURCE_ERROR: str = "source_error"


@dataclass(frozen=True)
class MemoryRecord:
    """One message to remember, plus what only the caller can know.

    ``NormalizedMessage`` carries what both readers can produce today. The two
    extra fields are for facts a source may have but the bridge's wire shape
    does not carry: a reply target, and whether a conversation is a group. Both
    default to ``None`` and both are stored as ``NULL`` when unknown, which is
    the honest answer and not the same as "no reply" or "not a group".
    """

    message: NormalizedMessage
    reply_to_source_id: str | None = None
    conversation_kind: str | None = None


@dataclass
class IngestionReport:
    """What one run did. Counts and fixed tokens only -- never content."""

    run_id: str
    source: str
    state: str
    started_at: float
    completed_at: float
    conversations_seen: int = 0
    messages_seen: int = 0
    messages_inserted: int = 0
    messages_updated: int = 0
    duplicates_detected: int = 0
    failure_state: str | None = None
    coverage: tuple[CoverageRecord, ...] = field(default_factory=tuple)

    @property
    def succeeded(self) -> bool:
        return self.state == RUN_SUCCEEDED


class MemoryIngestor:
    """Writes canonical memory from normalised messages.

    Holds an open :class:`memory_store.MemoryStore`, which has already passed
    the consent gate. It does not open one itself, so there is no path through
    ingestion that skips consent.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    # -- the explicit-batch API ---------------------------------------------

    def ingest(
        self,
        source: str,
        records: Iterable[MemoryRecord | NormalizedMessage],
        *,
        conversations: Sequence[NormalizedConversation] = (),
        coverage: Sequence[CoverageRecord] = (),
        identity_mode: str | None = None,
        timestamp_kind: str | None = None,
        run_id: str | None = None,
        now: float | None = None,
    ) -> IngestionReport:
        """Ingests an explicit batch in one transaction.

        ``coverage`` is supplied by the caller because only the caller knows
        what it actually looked at. A batch with no coverage records is
        accepted -- the messages are still canonical -- but the store will then
        answer ``not_observed`` for every window, which is the correct answer
        for a batch that never said what it covered.
        """
        moment = time.time() if now is None else now
        identity = identity_mode or DEFAULT_IDENTITY_MODES.get(source, IDENTITY_DERIVED)
        if identity not in IDENTITY_MODES:
            raise MemoryStoreError(
                "identity_mode_unknown", "The requested identity mode is not recognised."
            )
        stamp_kind = timestamp_kind or DEFAULT_TIMESTAMP_KINDS.get(
            source, TIME_SOURCE_REPORTED
        )
        run = run_id or new_run_id(moment)
        prepared = list(records)

        self._store.begin_run(run, source, moment)
        try:
            with self._store.transaction():
                report = self._write(
                    run=run,
                    source=source,
                    identity=identity,
                    stamp_kind=stamp_kind,
                    records=prepared,
                    conversations=conversations,
                    coverage=coverage,
                    moment=moment,
                )
        except Exception as error:
            # The run row itself is written outside the batch transaction so a
            # failed run leaves a record that it happened and failed. Losing
            # that would make a failure indistinguishable from never having
            # run, which is the coverage confusion this layer exists to stop.
            state = getattr(error, "state", "ingest_failed")
            self._store.finish_run(
                run,
                state=RUN_FAILED,
                completed_at=time.time() if now is None else moment,
                failure_state=state,
            )
            raise
        return report

    def _write(
        self,
        *,
        run: str,
        source: str,
        identity: str,
        stamp_kind: str,
        records: Sequence[MemoryRecord | NormalizedMessage],
        conversations: Sequence[NormalizedConversation],
        coverage: Sequence[CoverageRecord],
        moment: float,
    ) -> IngestionReport:
        normalised = [
            item if isinstance(item, MemoryRecord) else MemoryRecord(message=item)
            for item in records
        ]
        # Validate everything first. Nothing below this line may reject a
        # record, so a rejection cannot leave a partly written batch.
        for record in normalised:
            _validate(record, source)

        for conversation in conversations:
            self._store.upsert_conversation(
                canonical_id=conversation_canonical_id(source, str(conversation.id)),
                source=source,
                source_conversation_id=str(conversation.id),
                display_name=conversation.title,
                kind=None,
                first_seen_at=conversation.first_seen_at,
                last_seen_at=conversation.last_seen_at,
                now=moment,
            )

        inserted = updated = duplicates = 0
        touched_conversations: set[str] = {
            conversation_canonical_id(source, str(item.id)) for item in conversations
        }
        for record in normalised:
            message = record.message
            conversation_id = conversation_canonical_id(
                source, str(message.conversation_id)
            )
            if conversation_id not in touched_conversations:
                # A message can arrive for a conversation the caller did not
                # list. Record the conversation from what the message knows
                # rather than dropping the message or inventing a title.
                self._store.upsert_conversation(
                    canonical_id=conversation_id,
                    source=source,
                    source_conversation_id=str(message.conversation_id),
                    display_name=None,
                    kind=record.conversation_kind,
                    first_seen_at=None,
                    last_seen_at=message.first_observed_at,
                    now=moment,
                )
                touched_conversations.add(conversation_id)
            elif record.conversation_kind is not None:
                self._store.upsert_conversation(
                    canonical_id=conversation_id,
                    source=source,
                    source_conversation_id=str(message.conversation_id),
                    display_name=None,
                    kind=record.conversation_kind,
                    first_seen_at=None,
                    last_seen_at=None,
                    now=moment,
                )

            if identity == IDENTITY_SOURCE:
                source_message_id: str | None = str(message.id)
                canonical = message_canonical_id_from_source(source, source_message_id)
            else:
                source_message_id = None
                canonical = message_canonical_id_derived(
                    source,
                    conversation_id,
                    message.sender,
                    message.ownership,
                    message.kind,
                    message.text,
                    message.first_observed_at,
                )
            fingerprint = content_fingerprint(
                conversation_id,
                message.sender,
                message.ownership,
                message.kind,
                message.text,
                message.first_observed_at,
            )
            already_known = self._store.message_exists(canonical)
            if not already_known and any(
                holder != canonical
                for holder in self._store.fingerprint_holders(fingerprint)
            ):
                # Same apparent message, different canonical identity: a store
                # that was reset and re-read, or two sources describing one
                # message. Counted and left as two records. Merging here would
                # destroy the evidence that they came from different places.
                duplicates += 1

            reply_to = (
                message_canonical_id_from_source(source, record.reply_to_source_id)
                if record.reply_to_source_id is not None
                else None
            )
            outcome = self._store.upsert_message(
                {
                    "canonical_id": canonical,
                    "source": source,
                    "source_message_id": source_message_id,
                    "conversation_canonical_id": conversation_id,
                    "identity_mode": identity,
                    "content_fingerprint": fingerprint,
                    "sender": message.sender,
                    "ownership": message.ownership,
                    "sequence": message.sequence,
                    "timestamp": float(message.first_observed_at),
                    "timestamp_kind": stamp_kind,
                    "visible_time": message.visible_time,
                    "kind": message.kind,
                    "text": message.text,
                    "confidence": message.confidence,
                    "reply_to_canonical_id": reply_to,
                    "now": moment,
                    "run_id": run,
                }
            )
            if outcome == "inserted":
                inserted += 1
            else:
                updated += 1

        for record in coverage:
            self._store.record_coverage(run, record, moment)

        self._store.finish_run(
            run,
            state=RUN_SUCCEEDED,
            completed_at=moment,
            conversations_seen=len(touched_conversations),
            messages_seen=len(normalised),
            messages_inserted=inserted,
            messages_updated=updated,
            duplicates_detected=duplicates,
        )
        return IngestionReport(
            run_id=run,
            source=source,
            state=RUN_SUCCEEDED,
            started_at=moment,
            completed_at=moment,
            conversations_seen=len(touched_conversations),
            messages_seen=len(normalised),
            messages_inserted=inserted,
            messages_updated=updated,
            duplicates_detected=duplicates,
            coverage=tuple(coverage),
        )

    # -- the reader-driven API ----------------------------------------------

    def ingest_from_source(
        self,
        source: MessageSource,
        *,
        conversation_limit: int = 50,
        message_limit: int = 200,
        now: float | None = None,
    ) -> IngestionReport:
        """Reads a whole source once and remembers what it said.

        Coverage is recorded per conversation and reflects what happened:
        complete when the conversation returned fewer messages than the limit,
        partial when it filled the limit and therefore may have more above the
        fold. When the source refuses at any point, the run is recorded as
        failed with an ``unavailable`` coverage record and **nothing is
        written**, because a partial batch with a success record would
        overstate what we have.

        This method calls only the four questions in the ``MessageSource``
        protocol. It cannot widen the reader's surface, and it never falls back
        to another source.
        """
        moment = time.time() if now is None else now
        name = source.name
        run = new_run_id(moment)
        try:
            conversations = source.list_conversations(conversation_limit)
            batches: list[tuple[NormalizedConversation, list[NormalizedMessage]]] = [
                (conversation, list(source.get_messages(conversation.id, message_limit)))
                for conversation in conversations
            ]
        except MessageSourceError as error:
            self._store.begin_run(run, name, moment)
            with self._store.transaction():
                self._store.record_coverage(
                    run,
                    CoverageRecord(
                        source=name,
                        status=COVERAGE_UNAVAILABLE,
                        reason=REASON_SOURCE_ERROR,
                    ),
                    moment,
                )
            self._store.finish_run(
                run,
                state=RUN_FAILED,
                completed_at=moment,
                failure_state=error.state,
            )
            return IngestionReport(
                run_id=run,
                source=name,
                state=RUN_FAILED,
                started_at=moment,
                completed_at=moment,
                failure_state=error.state,
                coverage=(
                    CoverageRecord(
                        source=name,
                        status=COVERAGE_UNAVAILABLE,
                        reason=REASON_SOURCE_ERROR,
                    ),
                ),
            )

        records: list[MemoryRecord] = []
        coverage: list[CoverageRecord] = []
        for conversation, messages in batches:
            records.extend(MemoryRecord(message=message) for message in messages)
            stamps = [message.first_observed_at for message in messages]
            complete = len(messages) < message_limit
            coverage.append(
                CoverageRecord(
                    source=name,
                    status=COVERAGE_COMPLETE if complete else COVERAGE_PARTIAL,
                    conversation_canonical_id=conversation_canonical_id(
                        name, str(conversation.id)
                    ),
                    window_start=min(stamps) if stamps else None,
                    window_end=max(stamps) if stamps else None,
                    reason=None if complete else REASON_LIMIT_REACHED,
                    message_count=len(messages),
                )
            )
        return self.ingest(
            name,
            records,
            conversations=[conversation for conversation, _ in batches],
            coverage=coverage,
            run_id=run,
            now=moment,
        )


def _validate(record: MemoryRecord, source: str) -> None:
    """Rejects a record the store cannot represent honestly.

    Deliberately strict about the fields identity is built from. A message with
    no usable timestamp or no ownership would still insert, but every later
    answer about it -- ordering, windows, coverage -- would be a guess.
    """
    message = record.message
    if not isinstance(message, NormalizedMessage):
        raise MemoryStoreError(
            "record_malformed", "An ingested record was not a normalised message."
        )
    if message.source and message.source != source:
        raise MemoryStoreError(
            "record_source_mismatch",
            "An ingested record claims a different source than the batch.",
        )
    if not isinstance(message.conversation_id, int) or isinstance(
        message.conversation_id, bool
    ):
        raise MemoryStoreError(
            "record_malformed", "An ingested record has no usable conversation id."
        )
    if not isinstance(message.first_observed_at, (int, float)) or isinstance(
        message.first_observed_at, bool
    ):
        raise MemoryStoreError(
            "record_malformed", "An ingested record has no usable timestamp."
        )
    if not isinstance(message.ownership, str) or not message.ownership:
        raise MemoryStoreError(
            "record_malformed", "An ingested record has no ownership."
        )
    if not isinstance(message.kind, str) or not message.kind:
        raise MemoryStoreError("record_malformed", "An ingested record has no kind.")
    if message.text is not None and not isinstance(message.text, str):
        raise MemoryStoreError("record_malformed", "An ingested record has non-text text.")


__all__ = [
    "DEFAULT_IDENTITY_MODES",
    "DEFAULT_TIMESTAMP_KINDS",
    "REASON_LIMIT_REACHED",
    "REASON_SOURCE_ERROR",
    "IngestionReport",
    "MemoryIngestor",
    "MemoryRecord",
]
