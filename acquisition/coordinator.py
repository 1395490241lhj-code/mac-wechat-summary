"""Lease-scoped composition of the stored-key database acquisition path."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import tempfile
import time
from typing import Iterator

from .contracts import (
    AcquisitionOutcome,
    AcquisitionReadiness,
    AcquisitionState,
    OpaqueHandle,
    PreparedSource,
)
from .decryptor import (
    DatabaseDecryptError,
    DatabaseDecryptor,
    DatabaseFormatError,
    DatabaseKeyError,
)
from .snapshot import (
    EncryptedSource,
    SnapshotError,
    SnapshotFormatError,
    Snapshotter,
    replay_committed_wal,
)


_WORKSPACE_PREFIX = "acq-"


class AcquisitionCleanupError(Exception):
    def __init__(self) -> None:
        super().__init__("acquisition cleanup failed")


@dataclass(frozen=True, slots=True)
class AcquisitionSourceSet:
    message_sources: tuple[EncryptedSource, ...]
    conversation_identity_source: EncryptedSource | None = None
    display_identity_source: EncryptedSource | None = None

    def __post_init__(self) -> None:
        try:
            messages = tuple(self.message_sources)
        except TypeError:
            raise ValueError("acquisition source set invalid") from None
        if (not messages or not all(isinstance(source, EncryptedSource) for source in messages)
                or (self.conversation_identity_source is not None
                    and not isinstance(self.conversation_identity_source, EncryptedSource))
                or (self.display_identity_source is not None
                    and not isinstance(self.display_identity_source, EncryptedSource))):
            raise ValueError("acquisition source set invalid")
        object.__setattr__(self, "message_sources", messages)

    def ordered_sources(self) -> tuple[EncryptedSource, ...]:
        return self.message_sources + tuple(
            source for source in (
                self.conversation_identity_source, self.display_identity_source
            ) if source is not None
        )


def cleanup_stale_workspaces(
    root: Path,
    *,
    older_than_seconds: float = 24 * 60 * 60,
    now: float | None = None,
    limit: int = 16,
) -> int:
    if not root.is_dir() or limit < 1:
        return 0
    cutoff = (time.time() if now is None else now) - older_than_seconds
    removed = 0
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if removed >= limit:
            break
        try:
            if (not child.name.startswith(_WORKSPACE_PREFIX) or child.is_symlink()
                    or not child.is_dir() or child.stat().st_mtime >= cutoff):
                continue
            shutil.rmtree(child)
            removed += 1
        except OSError:
            continue
    return removed


def _outcome(state: AcquisitionState, prepared: PreparedSource | None = None):
    return AcquisitionOutcome(AcquisitionReadiness(state), prepared)


def _valid_sqlite(path: Path) -> bool:
    connection = None
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        return connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    except sqlite3.Error:
        return False
    finally:
        if connection is not None:
            connection.close()


class AcquisitionCoordinator:
    def __init__(self, key_store, workspace_root: Path, *, snapshotter=None, decryptor=None):
        if not isinstance(workspace_root, Path):
            raise ValueError("acquisition workspace invalid")
        self._key_store = key_store
        self._root = workspace_root
        self._snapshotter = snapshotter or Snapshotter()
        self._decryptor = decryptor or DatabaseDecryptor()

    @contextmanager
    def prepare(
        self,
        source_set: AcquisitionSourceSet,
        *,
        database_mode_enabled: bool,
    ) -> Iterator[AcquisitionOutcome]:
        if not isinstance(source_set, AcquisitionSourceSet) or type(database_mode_enabled) is not bool:
            raise ValueError("acquisition request invalid")
        if not database_mode_enabled:
            yield _outcome(AcquisitionState.DISABLED)
            return
        sources = source_set.ordered_sources()
        try:
            secrets_by_descriptor = {}
            missing = False
            for source in sources:
                if source.key_descriptor not in secrets_by_descriptor:
                    secret = self._key_store.load(source.key_descriptor)
                    secrets_by_descriptor[source.key_descriptor] = secret
                    missing = missing or secret is None
            if missing:
                yield _outcome(AcquisitionState.NEEDS_BOOTSTRAP)
                return
        except Exception:
            yield _outcome(AcquisitionState.INTERNAL_ERROR)
            return

        lease = None
        outcome = _outcome(AcquisitionState.INTERNAL_ERROR)
        try:
            self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self._root, 0o700)
            cleanup_stale_workspaces(self._root)
            lease = Path(tempfile.mkdtemp(prefix=_WORKSPACE_PREFIX, dir=self._root))
            os.chmod(lease, 0o700)
            encrypted_dir = lease / secrets.token_hex(16)
            snapshots = self._snapshotter.copy(sources, encrypted_dir)
            plaintext_dir = lease / secrets.token_hex(16)
            plaintext_dir.mkdir(mode=0o700)
            plaintext_paths = []
            for snapshot in snapshots:
                replay_committed_wal(snapshot.main, snapshot.wal)
                plaintext = plaintext_dir / secrets.token_hex(16)
                self._decryptor.decrypt(
                    snapshot.main,
                    plaintext,
                    secrets_by_descriptor[snapshot.key_descriptor],
                )
                os.chmod(plaintext, 0o600)
                if not _valid_sqlite(plaintext):
                    raise DatabaseDecryptError()
                plaintext_paths.append(plaintext)
            message_count = len(source_set.message_sources)
            message_handles = tuple(
                OpaqueHandle(path) for path in plaintext_paths[:message_count]
            )
            index = message_count
            conversation_handle = None
            display_handle = None
            if source_set.conversation_identity_source is not None:
                conversation_handle = OpaqueHandle(plaintext_paths[index])
                index += 1
            if source_set.display_identity_source is not None:
                display_handle = OpaqueHandle(plaintext_paths[index])
            outcome = _outcome(AcquisitionState.READY, PreparedSource(
                message_handles, conversation_handle, display_handle
            ))
        except SnapshotError as error:
            state = (AcquisitionState.SOURCE_BUSY if str(error) == "source busy"
                     else AcquisitionState.SNAPSHOT_UNSTABLE)
            outcome = _outcome(state)
        except SnapshotFormatError:
            outcome = _outcome(AcquisitionState.VERSION_UNVERIFIED)
        except DatabaseKeyError:
            outcome = _outcome(AcquisitionState.NEEDS_BOOTSTRAP)
        except DatabaseFormatError:
            outcome = _outcome(AcquisitionState.VERSION_UNVERIFIED)
        except DatabaseDecryptError:
            outcome = _outcome(AcquisitionState.DECRYPT_FAILED)
        except Exception:
            outcome = _outcome(AcquisitionState.INTERNAL_ERROR)
        try:
            yield outcome
        finally:
            if lease is not None:
                try:
                    shutil.rmtree(lease)
                except OSError:
                    raise AcquisitionCleanupError() from None
