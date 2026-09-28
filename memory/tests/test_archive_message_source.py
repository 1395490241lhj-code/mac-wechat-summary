from __future__ import annotations

import sqlite3

import memory_identity as identity
from archive_message_source import ArchiveMessageSource, SOURCE_ARCHIVE
from memory_ingest import MemoryIngestor
from message_source import COVERAGE_COMPLETE, COVERAGE_PARTIAL


def seed_archive_store(path):
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA user_version = 4;
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            title TEXT,
            first_seen_at REAL,
            last_seen_at REAL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            conversation_id INTEGER,
            sequence INTEGER,
            sender TEXT,
            ownership TEXT,
            visible_time TEXT,
            text TEXT,
            kind TEXT,
            confidence REAL,
            first_observed_at REAL
        );
        CREATE TABLE archive_conversations (
            id INTEGER PRIMARY KEY,
            source_conversation_key TEXT NOT NULL
        );
        CREATE TABLE archive_imports (
            id INTEGER PRIMARY KEY,
            archive_conversation_id INTEGER NOT NULL,
            transcript_shape TEXT NOT NULL,
            imported_at REAL NOT NULL
        );
        CREATE TABLE archive_attributed_records (
            import_id INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            sender TEXT NOT NULL,
            sent_at REAL NOT NULL,
            sent_at_text TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE archive_unattributed_records (
            import_id INTEGER NOT NULL,
            sequence INTEGER NOT NULL,
            record_text TEXT NOT NULL
        );
        CREATE TABLE archive_conversation_links (
            archive_conversation_id INTEGER PRIMARY KEY,
            visual_conversation_id INTEGER NOT NULL,
            basis TEXT NOT NULL,
            asserted_at REAL NOT NULL
        );
        CREATE TABLE archive_attachment_batches (
            id INTEGER PRIMARY KEY,
            import_id INTEGER NOT NULL,
            batch_fingerprint TEXT NOT NULL,
            observed_at REAL NOT NULL,
            attachment_count INTEGER NOT NULL,
            materialized_count INTEGER NOT NULL
        );
        CREATE TABLE archive_attachments (
            id INTEGER PRIMARY KEY,
            batch_id INTEGER NOT NULL,
            source_entry_index INTEGER NOT NULL,
            path_extension TEXT NOT NULL,
            byte_count INTEGER NOT NULL,
            crc32 INTEGER NOT NULL,
            media_kind TEXT,
            storage_state TEXT NOT NULL,
            content_sha256 TEXT,
            stored_relative_path TEXT,
            relation_scope TEXT NOT NULL
        );
        """
    )
    connection.executemany(
        "INSERT INTO archive_conversations(id, source_conversation_key) VALUES (?, ?)",
        [(1, "a"), (2, "b"), (3, "c")],
    )
    connection.executemany(
        """INSERT INTO archive_imports(
               id, archive_conversation_id, transcript_shape, imported_at
           ) VALUES (?, ?, ?, ?)""",
        [(1, 1, "attributed", 1000.0),
         (2, 2, "unattributed", 2000.0),
         (3, 3, "attributed", 3000.0)],
    )
    connection.executemany(
        """INSERT INTO archive_attributed_records(
               import_id, sequence, sender, sent_at, sent_at_text, text
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        [
            (1, 0, "A", 100.0, "t100", "one"),
            (1, 1, "B", 200.0, "t200", "two"),
            (3, 0, "C", 300.0, "t300", "three"),
        ],
    )
    connection.execute(
        "INSERT INTO archive_unattributed_records VALUES (2, 0, 'unattributed')"
    )
    connection.execute(
        "INSERT INTO archive_attachment_batches VALUES (1, 1, 'batch', 4000, 1, 1)"
    )
    connection.execute(
        """INSERT INTO archive_attachments(
               id, batch_id, source_entry_index, path_extension, byte_count,
               crc32, media_kind, storage_state, content_sha256,
               stored_relative_path, relation_scope
           ) VALUES (1, 1, 7, 'jpg', 12, 123, 'image', 'materialized',
                     'ATTACHMENT-SHA-SENTINEL', 'private/path', 'import_only')"""
    )
    connection.commit()
    connection.close()


def test_status_and_conversations_exclude_unattributed_exports(tmp_path):
    path = tmp_path / "messages.sqlite"
    seed_archive_store(path)
    source = ArchiveMessageSource(str(path))

    status = source.status()
    assert status.ready is True
    assert status.source == SOURCE_ARCHIVE
    assert status.conversation_count == 2
    assert status.message_count == 3

    result = source.list_conversations(10)
    assert [item.id for item in result.items] == [3, 1]
    assert result.coverage.status == COVERAGE_COMPLETE


def test_message_read_preserves_archive_attribution_and_time(tmp_path):
    path = tmp_path / "messages.sqlite"
    seed_archive_store(path)
    source = ArchiveMessageSource(str(path))

    result = source.get_messages(1, 10)

    assert [item.sender for item in result.items] == ["A", "B"]
    assert [item.text for item in result.items] == ["one", "two"]
    assert [item.first_observed_at for item in result.items] == [100.0, 200.0]
    assert [item.visible_time for item in result.items] == ["t100", "t200"]
    assert all(item.ownership == "unknown" for item in result.items)
    assert all(item.source == SOURCE_ARCHIVE for item in result.items)
    assert result.items[0].id != result.items[1].id
    assert result.coverage.status == COVERAGE_COMPLETE


def test_limits_report_partial_instead_of_claiming_complete(tmp_path):
    path = tmp_path / "messages.sqlite"
    seed_archive_store(path)
    source = ArchiveMessageSource(str(path))

    conversations = source.list_conversations(1)
    messages = source.get_messages(1, 1)

    assert len(conversations.items) == 1
    assert conversations.coverage.status == COVERAGE_PARTIAL
    assert conversations.coverage.truncated is True
    assert len(messages.items) == 1
    assert messages.coverage.status == COVERAGE_PARTIAL
    assert messages.coverage.truncated is True


def test_recent_messages_use_source_sent_time(tmp_path):
    path = tmp_path / "messages.sqlite"
    seed_archive_store(path)
    source = ArchiveMessageSource(str(path))

    result = source.get_recent_messages(150.0, 10)

    assert [item.text for item in result.items] == ["two", "three"]
    assert result.coverage.status == COVERAGE_COMPLETE


def test_memory_ingest_keeps_archive_as_its_own_source(tmp_path, store):
    path = tmp_path / "messages.sqlite"
    seed_archive_store(path)
    source = ArchiveMessageSource(str(path))

    report = MemoryIngestor(store).ingest_from_source(
        source, conversation_limit=10, message_limit=10, now=4000.0
    )

    assert report.succeeded
    assert report.source == SOURCE_ARCHIVE
    assert report.conversations_seen == 2
    assert report.messages_seen == 3

    canonical = identity.message_canonical_id_from_source(
        SOURCE_ARCHIVE, str((1 << 32) | 0)
    )
    row = store.message(canonical)
    assert row is not None
    assert row["source"] == SOURCE_ARCHIVE
    assert row["timestamp"] == 100.0
    assert row["timestamp_kind"] == "source_created"
    assert row["sender"] == "A"


def test_unattributed_export_is_not_promoted_into_memory(tmp_path, store):
    path = tmp_path / "messages.sqlite"
    seed_archive_store(path)
    source = ArchiveMessageSource(str(path))

    MemoryIngestor(store).ingest_from_source(
        source, conversation_limit=10, message_limit=10, now=4000.0
    )

    conversations = list(
        store.connection.execute(
            "SELECT source_conversation_id FROM conversations WHERE source = ?",
            (SOURCE_ARCHIVE,),
        )
    )
    assert {row["source_conversation_id"] for row in conversations} == {"1", "3"}

def test_worker_can_sync_the_archive_source_end_to_end(tmp_path):
    import io
    import json
    import memory_worker as worker
    from conftest import app_state

    app_store = tmp_path / "messages.sqlite"
    memory_store = tmp_path / "memory.sqlite"
    seed_archive_store(app_store)

    output = io.StringIO()
    request = {
        "op": "sync",
        "store_path": str(memory_store),
        "message_store_path": str(app_store),
        "message_source": SOURCE_ARCHIVE,
        "conversation_limit": 10,
        "message_limit": 10,
    }
    code = worker.main(
        io.StringIO(json.dumps(request)),
        output,
        read_app_consent_state=lambda: app_state(True),
    )
    reply = json.loads(output.getvalue())

    assert code == worker.EXIT_OK
    assert reply["ok"] is True
    assert reply["source"] == SOURCE_ARCHIVE
    assert reply["counts"]["conversations_seen"] == 2
    assert reply["counts"]["messages_seen"] == 3
    assert reply["counts"]["messages_inserted"] == 3


def test_schema_v3_without_link_table_is_refused(tmp_path):
    path = tmp_path / "messages.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA user_version = 3;
        CREATE TABLE conversations (id INTEGER PRIMARY KEY);
        CREATE TABLE messages (id INTEGER PRIMARY KEY);
        CREATE TABLE archive_conversations (id INTEGER PRIMARY KEY);
        CREATE TABLE archive_imports (id INTEGER PRIMARY KEY);
        CREATE TABLE archive_attributed_records (import_id INTEGER, sequence INTEGER);
        CREATE TABLE archive_unattributed_records (import_id INTEGER, sequence INTEGER);
        """
    )
    connection.commit()
    connection.close()

    status = ArchiveMessageSource(str(path)).status()

    assert status.ready is False
    assert status.state == "archive_schema_incomplete"


def test_v4_attachment_evidence_is_not_promoted_into_memory(tmp_path):
    path = tmp_path / "messages.sqlite"
    seed_archive_store(path)
    source = ArchiveMessageSource(str(path))

    conversations = source.list_conversations(10)
    messages = source.get_recent_messages(0.0, 10)

    rendered = repr(conversations.items) + repr(messages.items)
    assert "ATTACHMENT-SHA-SENTINEL" not in rendered
    assert "private/path" not in rendered
    assert [item.text for item in messages.items] == ["one", "two", "three"]


def test_schema_v4_requires_both_attachment_tables(tmp_path):
    for missing in ("archive_attachment_batches", "archive_attachments"):
        path = tmp_path / f"missing-{missing}.sqlite"
        seed_archive_store(path)
        connection = sqlite3.connect(path)
        connection.execute(f"DROP TABLE {missing}")
        connection.commit()
        connection.close()

        status = ArchiveMessageSource(str(path)).status()
        assert status.ready is False
        assert status.state == "archive_schema_incomplete"
