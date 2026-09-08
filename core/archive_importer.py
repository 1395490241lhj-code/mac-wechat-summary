"""Import native WeChat chat-export ZIPs (for example produced by Dukou).

This is a second, independent ingestion path that has nothing to do with the
existing WeChat database reader (``core/wechat_db.py``, ``core/decryptor.py``,
``core/key_extractor.py``).  It exists because the database route depends on
obtaining access material that newer WeChat builds may not surrender, while
WeChat itself will happily produce an official export ZIP through
"合并转发 → 转发到其他应用".

The importer treats the original ZIP as the source of truth: validate it, keep
a content-addressed raw copy forever, parse the native TXT transcript, and
materialize normalized text messages into a local SQLite archive.

Phase A is text only.  Attachments (images, video, files) are deliberately not
mapped to messages yet -- see ``_ATTACHMENT_EXTENSION_POINT``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import time
import zipfile
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

DEFAULT_ARCHIVE_DIR = os.path.expanduser("~/.wechat-summary/archive")

MAX_ARCHIVE_ENTRIES = 1000
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024  # 1 GiB
MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024  # 16 MiB per TXT we are willing to parse
_ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}

# ``·<sender>\n<yyyy年M月d日 HH:mm>\n`` starts every record; the body is
# everything up to the next such header.
_RECORD_HEADER = re.compile(
    r"(?m)^·([^\n]+)\n(\d{4}年\d{1,2}月\d{1,2}日 \d{1,2}:\d{2})\n"
)
_TIMESTAMP_FORMAT = "%Y年%m月%d日 %H:%M"

SCHEMA_VERSION = 1

# _ATTACHMENT_EXTENSION_POINT
# Phase B will map the non-TXT entries of the ZIP (images / video / files) onto
# messages.  The raw ZIP is already preserved verbatim and content-addressed, so
# that work needs no re-import: it can read attachments straight out of
# ``archives.raw_path`` once a real export has told us how WeChat names them and
# how the TXT refers to them.  Nothing here should be guessed before then.


class ArchiveImportError(ValueError):
    """The supplied archive is not a supported/valid native WeChat export."""


@dataclass(frozen=True)
class TranscriptRecord:
    """One parsed message, in transcript order."""

    sender: str
    timestamp: int
    timestamp_text: str
    text: str
    sequence: int


@dataclass(frozen=True)
class ImportResult:
    archive_id: str
    raw_path: str
    conversation_key: str
    conversation_name: str
    transcript_entry: str
    archive_message_count: int
    inserted_message_count: int
    existing_archive: bool


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_transcript(body: str) -> list[TranscriptRecord]:
    """Parse WeChat's native TXT transcript format.

    Supports multi-line bodies, empty bodies, CRLF, a UTF-8 BOM, Unicode and
    emoji.  Raises :class:`ArchiveImportError` if the text is not a native
    transcript.
    """
    normalized = body.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    matches = list(_RECORD_HEADER.finditer(normalized))
    # Everything before the first record header must be blank -- otherwise this
    # is some other TXT that merely happens to contain a WeChat-looking line.
    if not matches or normalized[: matches[0].start()].strip():
        raise ArchiveImportError("TXT is not a recognizable native WeChat transcript")

    records: list[TranscriptRecord] = []
    for sequence, match in enumerate(matches):
        timestamp_text = match.group(2)
        try:
            parsed = datetime.strptime(timestamp_text, _TIMESTAMP_FORMAT)
        except ValueError as exc:
            raise ArchiveImportError("transcript contains an invalid timestamp") from exc
        end = matches[sequence + 1].start() if sequence + 1 < len(matches) else len(normalized)
        records.append(
            TranscriptRecord(
                sender=match.group(1).strip(),
                timestamp=int(parsed.timestamp()),
                timestamp_text=timestamp_text,
                text=normalized[match.end() : end].strip("\n"),
                sequence=sequence,
            )
        )
    return records


# --------------------------------------------------------------------------
# ZIP validation
# --------------------------------------------------------------------------


def _validate_entry(info: zipfile.ZipInfo) -> None:
    name = info.filename.replace("\\", "/")
    if not name:
        raise ArchiveImportError("archive contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or name.startswith("/"):
        raise ArchiveImportError("archive contains an absolute path")
    if any(part == ".." for part in path.parts):
        raise ArchiveImportError("archive contains a path traversal entry")
    if ":" in path.parts[0]:
        raise ArchiveImportError("archive contains a drive-qualified path")
    if info.flag_bits & 0x1:
        raise ArchiveImportError("encrypted ZIP entries are not supported")
    if info.compress_type not in _ALLOWED_COMPRESSION:
        raise ArchiveImportError("unsupported ZIP compression method")
    if info.create_system == 3 and stat.S_ISLNK(info.external_attr >> 16):
        raise ArchiveImportError("symlink ZIP entries are not supported")


def read_wechat_archive(path: str | os.PathLike[str]) -> tuple[str, list[TranscriptRecord]]:
    """Validate a native WeChat ZIP and return ``(entry_name, records)``.

    The transcript chosen is the recognizable WeChat TXT with the most
    messages.  Unrecognizable TXTs are ignored rather than concatenated.
    """
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveImportError("not a valid ZIP archive") from exc

    with zf:
        infos = zf.infolist()
        if not 1 <= len(infos) <= MAX_ARCHIVE_ENTRIES:
            raise ArchiveImportError("archive entry count is outside the supported range")

        total = 0
        transcripts: list[zipfile.ZipInfo] = []
        for info in infos:
            _validate_entry(info)
            if info.is_dir():
                continue
            total += info.file_size
            if total > MAX_EXPANDED_BYTES:
                raise ArchiveImportError("archive expands beyond the 1 GiB safety limit")
            if info.filename.lower().endswith(".txt") and info.file_size <= MAX_TRANSCRIPT_BYTES:
                transcripts.append(info)

        # Integrity before interpretation: every entry is CRC-checked before we
        # parse anything out of the archive.
        try:
            bad = zf.testzip()
        except (OSError, zipfile.BadZipFile) as exc:
            raise ArchiveImportError(f"archive is corrupt: {exc}") from exc
        if bad is not None:
            raise ArchiveImportError(f"CRC check failed for ZIP entry: {bad}")

        candidates: list[tuple[str, list[TranscriptRecord]]] = []
        for info in transcripts:
            try:
                body = zf.read(info).decode("utf-8-sig")
            except (OSError, zipfile.BadZipFile) as exc:
                raise ArchiveImportError(f"archive is corrupt: {exc}") from exc
            except UnicodeDecodeError:
                continue
            try:
                records = parse_transcript(body)
            except ArchiveImportError:
                continue
            if records:
                candidates.append((info.filename, records))

        if not candidates:
            raise ArchiveImportError("no recognizable native WeChat TXT transcript found")
        return max(candidates, key=lambda item: len(item[1]))


# --------------------------------------------------------------------------
# Overlap merge
# --------------------------------------------------------------------------
#
# A WeChat export is a contiguous window of one ordered conversation.  Two
# exports of "the most recent N messages" therefore overlap heavily, and the
# only thing distinguishing two genuinely different messages that share sender,
# minute and text is *their position in the sequence*.
#
# A per-key "nth occurrence" counter is NOT sufficient: shift the export window
# past one copy of a repeated message and the counter renumbers, so a real new
# message collides with an already-stored one and is silently dropped.  (See
# ``test_shifted_window_does_not_drop_a_repeated_message``.)
#
# Instead we align sequences per minute bucket.  Messages sharing a
# ``timestamp_text`` are contiguous in a time-ordered transcript, and only the
# first and last bucket of an export can be truncated by the window, so a
# bucket-local alignment is enough to reconstruct the true order.
#
# KNOWN, INHERENT LIMIT.  When a minute bucket is *periodic* -- the same
# (sender, text) repeating, e.g. 哈哈 / 哈哈 / 哈哈, or an alternating a/b/a/b --
# two partially overlapping windows are genuinely indistinguishable from the
# same window imported twice.  ``[哈哈, 哈哈]`` twice is equally consistent with
# a history of two messages and one of three.  No algorithm can separate those
# without information the export does not carry, and idempotent re-import (the
# requirement that importing the same ZIP twice adds nothing) forces the
# conservative reading, so such a bucket can under-count.
#
# Measured on 200k randomized two-window trials over a deliberately degenerate
# alphabet (2 senders x 2 texts, buckets up to 7): 1.7% of buckets under-count,
# 93% of those inside a run of identical adjacent messages -- and *zero*
# spurious duplicates.  Real message text makes this vanishingly rare; it
# requires the same sender to send the identical text twice inside one minute
# with an export boundary landing between the copies.  The bias is deliberate:
# never invent a message, and prefer under-counting a repeated 哈哈 over
# duplicating the whole conversation on every re-import.

Bucket = list[tuple[str, str]]  # ordered (sender, text)


def _sublist_index(haystack: Bucket, needle: Bucket) -> int:
    """Index of ``needle`` as a contiguous sublist of ``haystack``, else -1."""
    if not needle:
        return 0
    limit = len(haystack) - len(needle)
    for start in range(limit + 1):
        if haystack[start : start + len(needle)] == needle:
            return start
    return -1


def merge_bucket(stored: Bucket, new: Bucket) -> tuple[Bucket, int, int]:
    """Merge two windows of the same minute bucket.

    Returns ``(merged, prepended, offset)`` where ``prepended`` is how many
    items were added in front of ``stored`` and ``offset`` is the index in
    ``merged`` at which ``new[0]`` lands.

    Preference order -- most confident alignment first, and when no alignment
    can be established we keep everything rather than risk deleting real
    messages.
    """
    if not stored:
        return list(new), 0, 0

    # 1. The new window is already fully contained: nothing to add.
    at = _sublist_index(stored, new)
    if at >= 0:
        return list(stored), 0, at

    # 2. The stored window was a truncated view of this bucket; the new one
    #    contains it whole.
    at = _sublist_index(new, stored)
    if at >= 0:
        return list(new), at, 0

    # 3/4. Otherwise the windows can only abut.  Take the longest overlap in
    #      either direction; an equal-length overlap both ways is genuinely
    #      ambiguous, and appending is the arbitrary but stable choice.
    span = min(len(stored), len(new))
    tail = next((k for k in range(span, 0, -1) if stored[-k:] == new[:k]), 0)
    head = next((k for k in range(span, 0, -1) if new[-k:] == stored[:k]), 0)

    if head > tail:
        prefix = list(new[: len(new) - head])
        return prefix + list(stored), len(prefix), 0

    # 5. ``tail == 0`` means no overlap at all between two windows of the same
    #    minute.  Ambiguous; append rather than drop anything.
    return list(stored) + list(new[tail:]), 0, len(stored) - tail


def _contiguous_runs(records: list[TranscriptRecord]) -> list[tuple[str, list[TranscriptRecord]]]:
    runs: list[tuple[str, list[TranscriptRecord]]] = []
    for record in records:
        if runs and runs[-1][0] == record.timestamp_text:
            runs[-1][1].append(record)
        else:
            runs.append((record.timestamp_text, [record]))
    return runs


def _occurrences(bucket: Bucket) -> list[int]:
    seen: dict[tuple[str, str], int] = {}
    out = []
    for item in bucket:
        count = seen.get(item, 0)
        out.append(count)
        seen[item] = count + 1
    return out


def message_fingerprint(
    conversation_key: str, sender: str, timestamp_text: str, text: str, occurrence: int
) -> str:
    payload = json.dumps(
        [conversation_key, sender, timestamp_text, text, occurrence],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


class ArchiveStore:
    """Persistent raw archive + normalized message store."""

    def __init__(self, root: str | os.PathLike[str] = DEFAULT_ARCHIVE_DIR):
        self.root = Path(root).expanduser()
        self.raw_dir = self.root / "raw"
        self.db_path = self.root / "messages.sqlite3"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        _chmod(self.root, 0o700)
        _chmod(self.raw_dir, 0o700)
        self._init_db()
        _chmod(self.db_path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with closing(self._connect()) as conn, conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS archives (
                    archive_id        TEXT PRIMARY KEY,
                    conversation_key  TEXT NOT NULL,
                    conversation_name TEXT NOT NULL,
                    original_name     TEXT NOT NULL,
                    raw_path          TEXT NOT NULL,
                    transcript_entry  TEXT NOT NULL,
                    message_count     INTEGER NOT NULL,
                    imported_at       INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_key TEXT NOT NULL,
                    sender           TEXT NOT NULL,
                    timestamp        INTEGER NOT NULL,
                    timestamp_text   TEXT NOT NULL,
                    text             TEXT NOT NULL,
                    position         INTEGER NOT NULL,
                    occurrence       INTEGER NOT NULL,
                    fingerprint      TEXT NOT NULL,
                    UNIQUE (conversation_key, timestamp_text, position)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
                    ON messages(conversation_key, timestamp, position, id);
                CREATE INDEX IF NOT EXISTS idx_messages_fingerprint
                    ON messages(fingerprint);

                CREATE TABLE IF NOT EXISTS archive_messages (
                    archive_id TEXT NOT NULL REFERENCES archives(archive_id) ON DELETE CASCADE,
                    sequence   INTEGER NOT NULL,
                    message_id INTEGER NOT NULL REFERENCES messages(id),
                    PRIMARY KEY (archive_id, sequence)
                );
                """
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- import ------------------------------------------------------------

    def import_zip(
        self,
        zip_path: str | os.PathLike[str],
        *,
        conversation_name: str,
        conversation_key: str | None = None,
    ) -> ImportResult:
        source = Path(zip_path).expanduser()
        if not source.is_file():
            raise ArchiveImportError(f"archive does not exist: {source}")
        conversation_name = conversation_name.strip()
        if not conversation_name:
            raise ArchiveImportError("conversation_name is required")
        conversation_key = (conversation_key or f"name:{conversation_name}").strip()
        if not conversation_key:
            raise ArchiveImportError("conversation_key is required")

        archive_id = _sha256_file(source)
        existing = self._existing_archive(archive_id)
        if existing is not None:
            return existing

        # Preserve first, then parse the preserved copy: the ZIP we keep is
        # exactly the bytes we imported, and nothing afterwards depends on the
        # caller's (possibly temporary) Dukou file still existing.
        pending = self._stage_raw(source, archive_id)
        try:
            transcript_entry, records = read_wechat_archive(pending)
            raw_path = self._promote_raw(pending, archive_id)
        except BaseException:
            pending.unlink(missing_ok=True)
            raise

        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO archives(
                       archive_id, conversation_key, conversation_name, original_name,
                       raw_path, transcript_entry, message_count, imported_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    archive_id,
                    conversation_key,
                    conversation_name,
                    source.name,
                    str(raw_path),
                    transcript_entry,
                    len(records),
                    int(time.time()),
                ),
            )
            inserted = self._merge_records(conn, archive_id, conversation_key, records)

        return ImportResult(
            archive_id=archive_id,
            raw_path=str(raw_path),
            conversation_key=conversation_key,
            conversation_name=conversation_name,
            transcript_entry=transcript_entry,
            archive_message_count=len(records),
            inserted_message_count=inserted,
            existing_archive=False,
        )

    def _existing_archive(self, archive_id: str) -> ImportResult | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT raw_path, transcript_entry, message_count, conversation_key, "
                "conversation_name FROM archives WHERE archive_id = ?",
                (archive_id,),
            ).fetchone()
        if row is None:
            return None
        return ImportResult(
            archive_id=archive_id,
            raw_path=row["raw_path"],
            conversation_key=row["conversation_key"],
            conversation_name=row["conversation_name"],
            transcript_entry=row["transcript_entry"],
            archive_message_count=row["message_count"],
            inserted_message_count=0,
            existing_archive=True,
        )

    def _merge_records(
        self,
        conn: sqlite3.Connection,
        archive_id: str,
        conversation_key: str,
        records: list[TranscriptRecord],
    ) -> int:
        inserted = 0
        for timestamp_text, run in _contiguous_runs(records):
            timestamp = run[0].timestamp
            rows = conn.execute(
                "SELECT id, position, sender, text FROM messages "
                "WHERE conversation_key = ? AND timestamp_text = ? ORDER BY position",
                (conversation_key, timestamp_text),
            ).fetchall()
            stored: Bucket = [(r["sender"], r["text"]) for r in rows]
            base = rows[0]["position"] if rows else 0
            ids_by_position = {r["position"]: r["id"] for r in rows}

            new: Bucket = [(r.sender, r.text) for r in run]
            merged, prepended, offset = merge_bucket(stored, new)
            start = base - prepended

            occurrences = _occurrences(merged)
            for index, (sender, text) in enumerate(merged):
                position = start + index
                occurrence = occurrences[index]
                fingerprint = message_fingerprint(
                    conversation_key, sender, timestamp_text, text, occurrence
                )
                message_id = ids_by_position.get(position)
                if message_id is None:
                    cursor = conn.execute(
                        """INSERT INTO messages(
                               conversation_key, sender, timestamp, timestamp_text,
                               text, position, occurrence, fingerprint
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            conversation_key,
                            sender,
                            timestamp,
                            timestamp_text,
                            text,
                            position,
                            occurrence,
                            fingerprint,
                        ),
                    )
                    ids_by_position[position] = int(cursor.lastrowid)
                    inserted += 1
                else:
                    # A prepend shifts which copy of a repeated message is the
                    # first one, so the derived columns are recomputed.
                    conn.execute(
                        "UPDATE messages SET occurrence = ?, fingerprint = ? "
                        "WHERE id = ? AND (occurrence != ? OR fingerprint != ?)",
                        (occurrence, fingerprint, message_id, occurrence, fingerprint),
                    )

            for index, record in enumerate(run):
                conn.execute(
                    "INSERT OR REPLACE INTO archive_messages(archive_id, sequence, message_id) "
                    "VALUES (?, ?, ?)",
                    (archive_id, record.sequence, ids_by_position[start + offset + index]),
                )
        return inserted

    # -- raw preservation --------------------------------------------------

    def _raw_target(self, archive_id: str) -> Path:
        return self.raw_dir / archive_id[:2] / f"{archive_id}.zip"

    def _stage_raw(self, source: Path, archive_id: str) -> Path:
        pending = self.raw_dir / f"{archive_id}.{os.getpid()}.part"
        shutil.copyfile(source, pending)
        _chmod(pending, 0o600)
        if _sha256_file(pending) != archive_id:
            pending.unlink(missing_ok=True)
            raise ArchiveImportError("raw archive copy failed its integrity check")
        return pending

    def _promote_raw(self, pending: Path, archive_id: str) -> Path:
        target = self._raw_target(archive_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        _chmod(target.parent, 0o700)
        os.replace(pending, target)
        _chmod(target, 0o600)
        return target

    # -- reads -------------------------------------------------------------

    def message_count(self, conversation_key: str) -> int:
        with closing(self._connect()) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_key = ?",
                    (conversation_key,),
                ).fetchone()[0]
            )

    def messages(self, conversation_key: str) -> list[sqlite3.Row]:
        """All stored messages for a conversation, in reconstructed order."""
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM messages WHERE conversation_key = ? "
                "ORDER BY timestamp, position, id",
                (conversation_key,),
            ).fetchall()


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _main() -> int:
    parser = argparse.ArgumentParser(description="Import a Dukou/native WeChat chat ZIP")
    parser.add_argument("zip_path")
    parser.add_argument("--chat", required=True, help="Conversation/group display name")
    parser.add_argument("--conversation-key", help="Stable conversation id, if one is known")
    parser.add_argument("--root", default=DEFAULT_ARCHIVE_DIR, help="Archive store directory")
    args = parser.parse_args()

    try:
        result = ArchiveStore(args.root).import_zip(
            args.zip_path,
            conversation_name=args.chat,
            conversation_key=args.conversation_key,
        )
    except ArchiveImportError as exc:
        parser.error(str(exc))
        return 2
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
