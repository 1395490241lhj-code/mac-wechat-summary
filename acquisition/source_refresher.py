"""Bounded source-growth refresh for recorded WeChat database message shards."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .coordinator import AcquisitionSourceSet
from .snapshot import EncryptedSource


class SourceRefresher(Protocol):
    def refresh(self, source_set: AcquisitionSourceSet) -> AcquisitionSourceSet:
        ...


class BoundedSourceRefresher:
    """Refreshes message shards within the explicitly recorded message directory.

    WeChat creates new message shards (message_1.db, message_2.db, ...) as chat
    history grows. Because the account root was explicitly validated during
    bootstrap, this refresher enumerates only that already-selected message
    directory for *.db shards, binding each to the recorded message-role descriptor.

    Anchors (session.db and contact.db) are never discovered or changed here.
    """

    def refresh(self, source_set: AcquisitionSourceSet) -> AcquisitionSourceSet:
        if not isinstance(source_set, AcquisitionSourceSet) or not source_set.message_sources:
            return source_set

        first_main = source_set.message_sources[0].main
        if first_main is None:
            return source_set
        message_dir = first_main.parent
        if not message_dir.is_dir():
            return source_set

        # Verify all existing recorded message shards belong to this exact directory
        for s in source_set.message_sources:
            if s.main is None or s.main.parent != message_dir:
                return source_set

        # Bounded scan: only *.db files directly in message_dir
        descriptor = source_set.message_sources[0].key_descriptor
        db_paths = sorted(p for p in message_dir.glob("*.db") if p.is_file() and not p.name.startswith("."))
        if not db_paths:
            return source_set

        refreshed_messages = tuple(
            EncryptedSource(
                path,
                path.with_name(path.name + "-wal") if path.with_name(path.name + "-wal").is_file() else None,
                path.with_name(path.name + "-shm") if path.with_name(path.name + "-shm").is_file() else None,
                descriptor,
            )
            for path in db_paths
        )

        return AcquisitionSourceSet(
            refreshed_messages,
            source_set.conversation_identity_source,
            source_set.display_identity_source,
        )
