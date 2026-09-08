"""Import native WeChat chat-export ZIPs (for example from Dukou).

The importer deliberately treats the original ZIP as the source of truth:
validate it, keep a content-addressed raw copy, parse the native TXT transcript,
and materialize normalized text messages into a local SQLite archive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import time
import zipfile

DEFAULT_ARCHIVE_DIR = os.path.expanduser("~/.wechat-summary/archive")
MAX_ARCHIVE_ENTRIES = 1000
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024
_ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class ArchiveImportError(ValueError):
    """The supplied archive is not a supported/valid native WeChat export."""


@dataclass(frozen=True)
class TranscriptRecord:
    sender: str
    timestamp: int
    timestamp_text: str
    text: str
    sequence: int
    occurrence: int


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


class ArchiveStore:
    """Persistent raw archive + normalized message store."""

    def __init__(self, root: str | os.PathLike[str] = DEFAULT_ARCHIVE_DIR):
        self.root = Path(root).expanduser()
        self.raw_dir = self.root / "raw"
        self.db_path = self.root / "messages.sqlite3"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
            os.chmod(self.raw_dir, 0o700)
        except OSError:
            pass
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS archives (
                    archive_id TEXT PRIMARY KEY,
                    conversation_key TEXT NOT NULL,
                    conversation_name TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    raw_path TEXT NOT NULL,
                    transcript_entry TEXT NOT NULL,
                    message_count INTEGER NOT NULL,
                    imported_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_key TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    timestamp_text TEXT NOT NULL,
                    text TEXT NOT NULL,
                    occurrence INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
                    ON messages(conversation_key, timestamp, id);

                CREATE TABLE IF NOT EXISTS archive_messages (
                    archive_id TEXT NOT NULL REFERENCES archives(archive_id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    message_id INTEGER NOT NULL REFERENCES messages(id),
                    PRIMARY KEY (archive_id, sequence)
                );
                """
            )

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
        with self._connect() as conn:
            row = conn.execute(
                "SELECT raw_path, transcript_entry, message_count, conversation_key, conversation_name "
                "FROM archives WHERE archive_id = ?",
                (archive_id,),
            ).fetchone()
            if row is not None:
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

        transcript_entry, records = read_wechat_archive(source)
        raw_path = self._preserve_raw(source, archive_id)
        inserted = 0

        with self._connect() as conn:
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

            for record in records:
                fingerprint = _message_fingerprint(conversation_key, record)
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO messages(
                           conversation_key, sender, timestamp, timestamp_text,
                           text, occurrence, fingerprint
                       ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        conversation_key,
                        record.sender,
                        record.timestamp,
                        record.timestamp_text,
                        record.text,
                        record.occurrence,
                        fingerprint,
                    ),
                )
                inserted += 1 if cursor.rowcount == 1 else 0
                message_id = conn.execute(
                    "SELECT id FROM messages WHERE fingerprint = ?", (fingerprint,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO archive_messages(archive_id, sequence, message_id) VALUES (?, ?, ?)",
                    (archive_id, record.sequence, message_id),
                )

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

    def _preserve_raw(self, source: Path, archive_id: str) -> Path:
        folder = self.raw_dir / archive_id[:2]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{archive_id}.zip"
        if not target.exists():
            temp = target.with_suffix(".tmp")
            shutil.copyfile(source, temp)
            if _sha256_file(temp) != archive_id:
                temp.unlink(missing_ok=True)
                raise ArchiveImportError("raw archive copy failed integrity check")
            os.replace(temp, target)
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
        return target

    def message_count(self, conversation_key: str) -> int:
        with self._connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_key = ?",
                    (conversation_key,),
                ).fetchone()[0]
            )


def read_wechat_archive(path: str | os.PathLike[str]) -> tuple[str, list[TranscriptRecord]]:
    """Validate a native WeChat ZIP and return its best transcript."""
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ArchiveImportError("not a valid ZIP archive") from exc

    with zf:
        infos = zf.infolist()
        if not 1 <= len(infos) <= MAX_ARCHIVE_ENTRIES:
            raise ArchiveImportError("archive entry count is outside the supported range")

        total = 0
        candidates: list[tuple[str, list[TranscriptRecord]]] = []
        for info in infos:
            _validate_entry(info)
            if info.is_dir():
                continue
            total += info.file_size
            if total > MAX_EXPANDED_BYTES:
                raise ArchiveImportError("archive expands beyond the 1 GiB safety limit")
            if info.filename.lower().endswith(".txt") and info.file_size <= MAX_TRANSCRIPT_BYTES:
                try:
                    body = zf.read(info).decode("utf-8-sig")
                    records = parse_transcript(body)
                except (UnicodeDecodeError, ArchiveImportError, RuntimeError, zipfile.BadZipFile):
                    continue
                if records:
                    candidates.append((info.filename, records))

        bad = zf.testzip()
        if bad is not None:
            raise ArchiveImportError(f"CRC check failed for ZIP entry: {bad}")

        if not candidates:
            raise ArchiveImportError("no recognizable native WeChat TXT transcript found")
        return max(candidates, key=lambda item: len(item[1]))


def _validate_entry(info: zipfile.ZipInfo) -> None:
    name = info.filename.replace("\\", "/")
    if not name or name.startswith("/"):
        raise ArchiveImportError("archive contains an unsafe path")
    parts = PurePosixPath(name).parts
    if any(part == ".." for part in parts):
        raise ArchiveImportError("archive contains an unsafe path")
    if parts and ":" in parts[0]:
        raise ArchiveImportError("archive contains an unsafe path")
    if info.flag_bits & 0x1:
        raise ArchiveImportError("encrypted ZIP entries are not supported")
    if info.compress_type not in _ALLOWED_COMPRESSION:
        raise ArchiveImportError("unsupported ZIP compression method")


def parse_transcript(body: str) -> list[TranscriptRecord]:
    """Parse WeChat's native TXT transcript format used by macOS 4.1.13+."""
    import re

    normalized = body.replace("\r\n", "\n").lstrip("\ufeff")
    pattern = re.compile(r"(?m)^·([^\n]+)\n(\d{4}年\d{1,2}月\d{1,2}日 \d{2}:\d{2})\n")
    matches = list(pattern.finditer(normalized))
    if not matches or matches[0].start() != 0:
        raise ArchiveImportError("TXT is not a recognizable native WeChat transcript")

    occurrences: dict[tuple[str, str, str], int] = {}
    records: list[TranscriptRecord] = []
    for sequence, match in enumerate(matches):
        sender = match.group(1)
        timestamp_text = match.group(2)
        try:
            parsed = datetime.strptime(timestamp_text, "%Y年%m月%d日 %H:%M")
        except ValueError as exc:
            raise ArchiveImportError("transcript contains an invalid timestamp") from exc
        end = matches[sequence + 1].start() if sequence + 1 < len(matches) else len(normalized)
        text = normalized[match.end() : end].strip()
        key = (sender, timestamp_text, text)
        occurrence = occurrences.get(key, 0)
        occurrences[key] = occurrence + 1
        records.append(
            TranscriptRecord(
                sender=sender,
                timestamp=int(time.mktime(parsed.timetuple())),
                timestamp_text=timestamp_text,
                text=text,
                sequence=sequence,
                occurrence=occurrence,
            )
        )
    return records


def _message_fingerprint(conversation_key: str, record: TranscriptRecord) -> str:
    payload = json.dumps(
        [
            conversation_key,
            record.sender,
            record.timestamp_text,
            record.text,
            record.occurrence,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _main() -> int:
    parser = argparse.ArgumentParser(description="Import a Dukou/native WeChat chat ZIP")
    parser.add_argument("zip_path")
    parser.add_argument("--chat", required=True, help="Conversation/group display name")
    parser.add_argument("--conversation-key", help="Stable conversation id if known")
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
