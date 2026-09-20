"""The one recorded database source decision, read back as an acquisition input.

Database mode needs to know *where* the encrypted sources are before it can
acquire them. That answer is a decision the product already made once and
recorded: this module reads that record back and nothing else.

It is deliberately the smallest thing that can carry the answer.

* **Not discovery.** It reads one app-owned file at one path it was handed. It
  searches no directory, probes no home folder, and chooses between no
  accounts. The record names one already-selected source set or none.
* **Not secret storage.** The record holds provenance -- paths and key
  descriptors -- and never key bytes. Secret material stays in the KeyStore.
* **Not acquisition.** It opens no database, takes no snapshot, replays no WAL,
  decrypts nothing and contacts no provider. Whether a recorded source is
  *usable* is the acquisition layer's question; whether it is *well formed* is
  this one's.
* **Not a configuration framework.** One versioned record, one decision, no
  preferences, no defaults and no second active account.

A refusal is a fixed lowercase token chosen from :data:`SOURCE_STATES`. None
is assembled from the value it rejected, because a message built from a field
is how a filesystem path reaches a log through a validation error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .coordinator import AcquisitionSourceSet
from .keystore import KeyDescriptor
from .snapshot import EncryptedSource

#: The only record layout this reader understands. A record stamped otherwise
#: is refused rather than read optimistically: a changed layout must not be
#: accepted merely because today's parser can still find the fields it knows.
MANIFEST_VERSION: int = 1

#: The recorded decision named a usable source set.
SOURCE_READY: str = "ready"

#: No record exists. Database mode has never been established here.
SOURCE_ABSENT: str = "absent"

#: A record exists and is stamped with a layout this reader does not support.
SOURCE_UNSUPPORTED_VERSION: str = "unsupported_version"

#: A record exists, is stamped for this reader, and does not describe one
#: well-formed source set.
SOURCE_MALFORMED: str = "malformed"

#: The whole vocabulary, closed. A state outside this set is a defect.
SOURCE_STATES: frozenset[str] = frozenset({
    SOURCE_READY,
    SOURCE_ABSENT,
    SOURCE_UNSUPPORTED_VERSION,
    SOURCE_MALFORMED,
})

#: The selected-source token this record must carry. It exists so a record for
#: some other future decision cannot be read as a database decision by accident.
SELECTED_SOURCE_DATABASE: str = "database"

#: The three roles the record may describe, and the one that is required.
ROLE_MESSAGES: str = "messages"
ROLE_CONVERSATION_IDENTITY: str = "conversation_identity"
ROLE_DISPLAY_IDENTITY: str = "display_identity"


@dataclass(frozen=True, slots=True)
class LocatedSource:
    """The answer, and the record it came from.

    `source_set` is present exactly when `state` is :data:`SOURCE_READY`.
    The pairing is enforced at construction so a caller cannot hold a "ready"
    answer with nothing in it, or a refusal that quietly carries a source.
    """

    state: str
    source_set: AcquisitionSourceSet | None = None

    def __post_init__(self) -> None:
        if self.state not in SOURCE_STATES:
            raise ValueError("located source state invalid")
        if (self.state == SOURCE_READY) != (self.source_set is not None):
            raise ValueError("located source contradiction")
        if self.source_set is not None and not isinstance(
            self.source_set, AcquisitionSourceSet
        ):
            raise ValueError("located source contradiction")


def _entry_document(source: EncryptedSource) -> dict[str, object]:
    descriptor = source.key_descriptor
    return {
        "main": str(source.main),
        "wal": str(source.wal) if source.wal is not None else None,
        "shm": str(source.shm) if source.shm is not None else None,
        "key_descriptor": {
            "source_fingerprint": descriptor.source_fingerprint,
            "role_token": descriptor.role_token,
            "record_format_version": descriptor.record_format_version,
            "compatibility_token": descriptor.compatibility_token,
        },
    }


def record_document(source_set: AcquisitionSourceSet) -> dict[str, object]:
    """The record this reader consumes, written from a validated source set.

    Provenance only: paths and descriptors. It carries no key bytes and nothing
    derived from decrypted content, so writing and reading it never discloses a
    secret. Its shape is exactly the one SourceLocator validates.
    """
    return {
        "manifest_version": MANIFEST_VERSION,
        "selected_source": SELECTED_SOURCE_DATABASE,
        ROLE_MESSAGES: [
            _entry_document(source) for source in source_set.message_sources
        ],
        ROLE_CONVERSATION_IDENTITY: (
            _entry_document(source_set.conversation_identity_source)
            if source_set.conversation_identity_source is not None else None
        ),
        ROLE_DISPLAY_IDENTITY: (
            _entry_document(source_set.display_identity_source)
            if source_set.display_identity_source is not None else None
        ),
    }


def _optional_path(value: object) -> Path | None:
    """An absolute path, or `None` when the field is absent or null."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("located source path invalid")
    candidate = Path(value)
    if not candidate.is_absolute():
        raise ValueError("located source path invalid")
    return candidate


def _descriptor(value: object) -> KeyDescriptor:
    if not isinstance(value, dict):
        raise ValueError("located source descriptor invalid")
    return KeyDescriptor(
        value.get("source_fingerprint"),
        value.get("role_token"),
        value.get("record_format_version"),
        value.get("compatibility_token"),
    )


def _entry(value: object) -> EncryptedSource:
    """One role's encrypted source, structurally validated.

    Every branch raises a fixed token: a malformed record never reports which
    field was wrong, because the field holds a local filesystem path.
    """
    if not isinstance(value, dict):
        raise ValueError("located source role invalid")
    return EncryptedSource(
        _optional_path(value.get("main")),
        _optional_path(value.get("wal")),
        _optional_path(value.get("shm")),
        _descriptor(value.get("key_descriptor")),
    )


class SourceLocator:
    """Reads the recorded database source decision from one app-owned file."""

    def __init__(self, manifest_path: Path) -> None:
        if not isinstance(manifest_path, Path):
            raise ValueError("source locator path invalid")
        self._path = manifest_path

    def resolve(self) -> LocatedSource:
        """The recorded decision, or a fixed reason there is none.

        Reads one file and validates its shape. It performs no I/O beyond that
        read: no directory listing, no database, no decryption, no network.
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return LocatedSource(SOURCE_ABSENT)
        except (OSError, UnicodeDecodeError):
            return LocatedSource(SOURCE_MALFORMED)
        try:
            document = json.loads(raw)
        except ValueError:
            return LocatedSource(SOURCE_MALFORMED)
        return self._read(document)

    def _read(self, document: object) -> LocatedSource:
        if not isinstance(document, dict):
            return LocatedSource(SOURCE_MALFORMED)
        version = document.get("manifest_version")
        if isinstance(version, bool) or version != MANIFEST_VERSION:
            return LocatedSource(SOURCE_UNSUPPORTED_VERSION)
        if document.get("selected_source") != SELECTED_SOURCE_DATABASE:
            return LocatedSource(SOURCE_MALFORMED)
        messages = document.get(ROLE_MESSAGES)
        if not isinstance(messages, list) or not messages:
            return LocatedSource(SOURCE_MALFORMED)
        try:
            message_sources = tuple(_entry(entry) for entry in messages)
            conversation = self._optional_role(document.get(ROLE_CONVERSATION_IDENTITY))
            display = self._optional_role(document.get(ROLE_DISPLAY_IDENTITY))
            mains = [
                source.main
                for source in (
                    *message_sources,
                    *(source for source in (conversation, display) if source is not None),
                )
            ]
            if len(set(mains)) != len(mains):
                raise ValueError("located source role duplicate")
            return LocatedSource(
                SOURCE_READY,
                AcquisitionSourceSet(message_sources, conversation, display),
            )
        except ValueError:
            return LocatedSource(SOURCE_MALFORMED)

    @staticmethod
    def _optional_role(value: object) -> EncryptedSource | None:
        if value is None:
            return None
        return _entry(value)
