"""Read-only Memory source over attributed WeChat archive evidence.

Each archive import remains its own conversation observation. This adapter never
links an import to visual capture, never reads provider filenames, and never
projects unattributed records into a timestamped message model.
"""

from __future__ import annotations

import os
import sqlite3
from urllib.parse import quote

try:
    from message_source import (
        COVERAGE_COMPLETE,
        COVERAGE_PARTIAL,
        REASON_CALLER_LIMIT,
        REASON_EMPTY_WINDOW,
        REASON_FULL_WINDOW_OBSERVED,
        MessageSourceError,
        NormalizedConversation,
        NormalizedMessage,
        ReadCoverage,
        ReadFreshness,
        ReadResult,
        SourceStatus,
    )
except ImportError:  # pragma: no cover - source checkout import path
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "bridge"))
    from message_source import (
        COVERAGE_COMPLETE, COVERAGE_PARTIAL, REASON_CALLER_LIMIT,
        REASON_EMPTY_WINDOW, REASON_FULL_WINDOW_OBSERVED, MessageSourceError,
        NormalizedConversation, NormalizedMessage, ReadCoverage, ReadFreshness,
        ReadResult, SourceStatus,
    )

SOURCE_ARCHIVE = "archive"
_SUPPORTED_SCHEMA_VERSIONS = frozenset({2, 3, 4, 5})
_REQUIRED_TABLES_BY_VERSION = {
    2: frozenset({
        "conversations", "messages",
        "archive_conversations", "archive_imports",
        "archive_attributed_records", "archive_unattributed_records",
    }),
    3: frozenset({
        "conversations", "messages",
        "archive_conversations", "archive_imports",
        "archive_attributed_records", "archive_unattributed_records",
        "archive_conversation_links",
    }),
    4: frozenset({
        "conversations", "messages",
        "archive_conversations", "archive_imports",
        "archive_attributed_records", "archive_unattributed_records",
        "archive_conversation_links",
        "archive_attachment_batches", "archive_attachments",
    }),
    5: frozenset({
        "conversations", "messages",
        "archive_conversations", "archive_imports",
        "archive_attributed_records", "archive_unattributed_records",
        "archive_conversation_links",
        "archive_attachment_batches", "archive_attachments",
        "archive_conversation_labels",
    }),
}

def _message_id(import_id: int, sequence: int) -> int:
    if import_id <= 0 or sequence < 0:
        raise MessageSourceError(
            "archive_record_malformed", "Archive evidence contains an invalid identifier."
        )
    return (import_id << 32) | sequence


def _coverage(*, count: int, truncated: bool, observed: float | None) -> ReadCoverage:
    if truncated:
        return ReadCoverage(
            status=COVERAGE_PARTIAL,
            reason=REASON_CALLER_LIMIT,
            requested_start=None,
            requested_end=None,
            observed_through=observed,
            complete_through=None,
            freshness=ReadFreshness.UNKNOWN,
            truncated=True,
            item_count=count,
        )
    if count == 0:
        return ReadCoverage(
            status=COVERAGE_COMPLETE,
            reason=REASON_EMPTY_WINDOW,
            requested_start=None,
            requested_end=None,
            observed_through=None,
            complete_through=None,
            freshness=ReadFreshness.UNKNOWN,
            truncated=False,
            item_count=0,
        )
    return ReadCoverage(
        status=COVERAGE_COMPLETE,
        reason=REASON_FULL_WINDOW_OBSERVED,
        requested_start=None,
        requested_end=None,
        observed_through=observed,
        complete_through=observed,
        freshness=ReadFreshness.EVIDENCE_CONSISTENT,
        truncated=False,
        item_count=count,
    )


class ArchiveMessageSource:
    """Attributed archive evidence as an independent Memory source."""

    name = SOURCE_ARCHIVE

    def __init__(self, path: str) -> None:
        self._path = path

    def _connect(self) -> sqlite3.Connection:
        if not self._path or not os.path.isfile(self._path):
            raise MessageSourceError(
                "archive_store_missing", "The local archive store is not available."
            )
        try:
            connection = sqlite3.connect(
                f"file:{quote(self._path)}?mode=ro",
                uri=True,
                timeout=5.0,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000;")
            connection.execute("PRAGMA query_only = ON;")
            version = int(connection.execute("PRAGMA user_version;").fetchone()[0])
            if version not in _SUPPORTED_SCHEMA_VERSIONS:
                raise MessageSourceError(
                    "archive_schema_unsupported",
                    "The local archive store schema is not supported.",
                )
            present = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table';"
                )
            }
            required = _REQUIRED_TABLES_BY_VERSION[version]
            if not required.issubset(present):
                raise MessageSourceError(
                    "archive_schema_incomplete",
                    "The local archive store is missing required tables for its schema version.",
                )
            return connection
        except MessageSourceError:
            try:
                connection.close()
            except UnboundLocalError:
                pass
            raise
        except sqlite3.DatabaseError as error:
            raise MessageSourceError(
                "archive_store_unreadable", "The local archive store could not be read."
            ) from error

    def status(self) -> SourceStatus:
        try:
            connection = self._connect()
        except MessageSourceError as error:
            return SourceStatus(
                source=self.name, ready=False, state=error.state, detail=error.detail
            )
        try:
            version = int(connection.execute("PRAGMA user_version;").fetchone()[0])
            conversations = int(connection.execute(
                "SELECT COUNT(*) FROM archive_imports WHERE transcript_shape = 'attributed';"
            ).fetchone()[0])
            messages = int(connection.execute(
                """SELECT COUNT(*) FROM archive_attributed_records a
                   JOIN archive_imports i ON i.id = a.import_id
                   WHERE i.transcript_shape = 'attributed';"""
            ).fetchone()[0])
        finally:
            connection.close()
        return SourceStatus(
            source=self.name, ready=True, state="ready",
            schema_version=version, conversation_count=conversations,
            message_count=messages,
        )

    def list_conversations(self, limit: int) -> ReadResult[NormalizedConversation]:
        bounded = max(1, int(limit))
        connection = self._connect()
        try:
            version = int(connection.execute("PRAGMA user_version;").fetchone()[0])
            if version >= 5:
                sql = """SELECT i.id,
                                MIN(a.sent_at) AS first_sent_at,
                                MAX(a.sent_at) AS last_sent_at,
                                n.display_name
                         FROM archive_imports i
                         JOIN archive_attributed_records a ON a.import_id = i.id
                         JOIN archive_conversations c ON c.id = i.archive_conversation_id
                         LEFT JOIN archive_conversation_labels n
                                ON n.archive_conversation_id = c.id
                         WHERE i.transcript_shape = 'attributed'
                         GROUP BY i.id, n.display_name
                         ORDER BY i.imported_at DESC, i.id DESC
                         LIMIT ?;"""
            else:
                sql = """SELECT i.id,
                                MIN(a.sent_at) AS first_sent_at,
                                MAX(a.sent_at) AS last_sent_at,
                                NULL AS display_name
                         FROM archive_imports i
                         JOIN archive_attributed_records a ON a.import_id = i.id
                         WHERE i.transcript_shape = 'attributed'
                         GROUP BY i.id
                         ORDER BY i.imported_at DESC, i.id DESC
                         LIMIT ?;"""
            rows = connection.execute(sql, (bounded + 1,)).fetchall()
        finally:
            connection.close()
        truncated = len(rows) > bounded
        rows = rows[:bounded]
        items = tuple(
            NormalizedConversation(
                id=int(row["id"]),
                title=row["display_name"],
                first_seen_at=float(row["first_sent_at"]),
                last_seen_at=float(row["last_sent_at"]),
                source=self.name,
            )
            for row in rows
        )
        observed = max((item.last_seen_at for item in items), default=None)
        return ReadResult(
            items=items,
            coverage=_coverage(count=len(items), truncated=truncated, observed=observed),
        )

    def _conversation_exists(self, connection: sqlite3.Connection, import_id: int) -> bool:
        return connection.execute(
            "SELECT 1 FROM archive_imports WHERE id = ? AND transcript_shape = 'attributed';",
            (import_id,),
        ).fetchone() is not None

    def _message(self, row: sqlite3.Row) -> NormalizedMessage:
        import_id = int(row["import_id"])
        sequence = int(row["sequence"])
        return NormalizedMessage(
            id=_message_id(import_id, sequence),
            conversation_id=import_id,
            sequence=sequence,
            sender=row["sender"],
            ownership="unknown",
            visible_time=row["sent_at_text"],
            text=row["text"],
            kind="text",
            confidence=1.0,
            first_observed_at=float(row["sent_at"]),
            source=self.name,
        )

    def get_messages(
        self,
        conversation_id: int,
        limit: int,
        before_sequence: int | None = None,
    ) -> ReadResult[NormalizedMessage]:
        bounded = max(1, int(limit))
        connection = self._connect()
        try:
            if not self._conversation_exists(connection, int(conversation_id)):
                raise MessageSourceError(
                    "archive_conversation_unknown",
                    "The requested archive import is not available.",
                )
            clauses = ["import_id = ?"]
            params: list[int] = [int(conversation_id)]
            if before_sequence is not None:
                clauses.append("sequence < ?")
                params.append(int(before_sequence))
            params.append(bounded + 1)
            rows = connection.execute(
                f"""SELECT import_id, sequence, sender, sent_at, sent_at_text, text
                    FROM archive_attributed_records
                    WHERE {' AND '.join(clauses)}
                    ORDER BY sequence DESC
                    LIMIT ?;""",
                tuple(params),
            ).fetchall()
        finally:
            connection.close()
        truncated = len(rows) > bounded
        rows = rows[:bounded]
        items = tuple(self._message(row) for row in reversed(rows))
        observed = max((item.first_observed_at for item in items), default=None)
        return ReadResult(
            items=items,
            coverage=_coverage(count=len(items), truncated=truncated, observed=observed),
        )

    def get_recent_messages(
        self, since_observed_at: float, limit: int
    ) -> ReadResult[NormalizedMessage]:
        bounded = max(1, int(limit))
        connection = self._connect()
        try:
            rows = connection.execute(
                """SELECT a.import_id, a.sequence, a.sender, a.sent_at,
                          a.sent_at_text, a.text
                   FROM archive_attributed_records a
                   JOIN archive_imports i ON i.id = a.import_id
                   WHERE i.transcript_shape = 'attributed' AND a.sent_at >= ?
                   ORDER BY a.sent_at DESC, a.import_id DESC, a.sequence DESC
                   LIMIT ?;""",
                (float(since_observed_at), bounded + 1),
            ).fetchall()
        finally:
            connection.close()
        truncated = len(rows) > bounded
        rows = rows[:bounded]
        items = tuple(self._message(row) for row in reversed(rows))
        observed = max((item.first_observed_at for item in items), default=None)
        return ReadResult(
            items=items,
            coverage=_coverage(count=len(items), truncated=truncated, observed=observed),
        )
