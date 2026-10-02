"""Bounded source-growth refresh for recorded WeChat database message shards."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .coordinator import AcquisitionSourceSet
from .database_inventory import DatabaseInventory, inventory_message_directory
from .snapshot import EncryptedSource


class SourceRefresher(Protocol):
    def refresh(self, source_set: AcquisitionSourceSet) -> AcquisitionSourceSet:
        ...


class BoundedSourceRefresher:
    """Refreshes message shards within the explicitly recorded message directory.

    WeChat creates new message shards (message_1.db, message_2.db, ...) as chat
    history grows. Because the account root was explicitly validated during
    bootstrap, this refresher enumerates only that already-selected message
    directory. Each candidate is classified by the database-role inventory
    first, and only an ordinary message shard is bound to the recorded
    message-role descriptor. A business-message, search, media, auxiliary,
    unknown or unsupported-candidate database is never passed off as a shard;
    inventory() is how a caller sees them.

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

        # Bounded scan: direct children of message_dir only, and only the ones
        # the inventory recognises as ordinary message shards.
        descriptor = source_set.message_sources[0].key_descriptor
        db_paths = [
            message_dir / name
            for name in inventory_message_directory(message_dir).message_shards
        ]
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

    def inventory(self, source_set: AcquisitionSourceSet) -> DatabaseInventory:
        """The directory's role accounting: every candidate, exactly once.

        Never fails and never widens the scan. A source set this refresher
        would not act on reports an empty inventory rather than inspecting a
        directory it is not allowed to look at.
        """
        if not isinstance(source_set, AcquisitionSourceSet) or not source_set.message_sources:
            return DatabaseInventory()
        first_main = source_set.message_sources[0].main
        if first_main is None:
            return DatabaseInventory()
        message_dir = first_main.parent
        if not message_dir.is_dir():
            return DatabaseInventory()
        if any(source.main is None or source.main.parent != message_dir
               for source in source_set.message_sources):
            return DatabaseInventory()
        return inventory_message_directory(message_dir)
