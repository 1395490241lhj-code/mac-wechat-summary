from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import sqlite3
import time

import pytest


ROOT = Path(__file__).resolve().parents[2]


class FakeKeyStore:
    def __init__(self, values):
        self.values = values
        self.loads = []

    def load(self, descriptor):
        self.loads.append(descriptor)
        return self.values.get(descriptor)


class FakeDecryptor:
    def __init__(self, *, valid=True, error=None):
        self.valid = valid
        self.error = error
        self.calls = []

    def decrypt(self, source, output, secret):
        self.calls.append((source, output, secret))
        if self.error:
            raise self.error
        if not self.valid:
            output.write_bytes(b"not sqlite")
            return
        connection = sqlite3.connect(output)
        connection.execute("CREATE TABLE evidence(value TEXT)")
        connection.commit()
        connection.close()


def _descriptor(token="a"):
    from acquisition import KeyDescriptor

    return KeyDescriptor(token * 64, "messages", 1, "b" * 64)


def _source(tmp_path, name="one", descriptor=None):
    from acquisition import EncryptedSource

    main = tmp_path / name
    main.write_bytes(name.encode())
    return EncryptedSource(main, None, None, descriptor or _descriptor())


def _coordinator(tmp_path, sources, *, keys=None, decryptor=None):
    from acquisition import AcquisitionCoordinator, AcquisitionSourceSet, SecretBytes

    source_set = AcquisitionSourceSet(tuple(sources))
    store = FakeKeyStore(keys if keys is not None else {
        source.key_descriptor: SecretBytes(b"k" * 32) for source in sources
    })
    coordinator = AcquisitionCoordinator(
        store, tmp_path / "workspaces", decryptor=decryptor or FakeDecryptor()
    )
    return coordinator, source_set, store


def test_source_contract_is_immutable_explicit_and_requires_messages(tmp_path):
    from acquisition import AcquisitionSourceSet, EncryptedSource

    source = _source(tmp_path)
    source_set = AcquisitionSourceSet((source,))
    assert source_set.message_sources == (source,)
    assert source_set.conversation_identity_source is None
    assert source_set.display_identity_source is None
    assert "one" not in repr(source)
    with pytest.raises(FrozenInstanceError):
        source.main = tmp_path / "other"
    with pytest.raises(ValueError, match="^acquisition source set invalid$"):
        AcquisitionSourceSet(())
    with pytest.raises(ValueError, match="^encrypted source invalid$"):
        EncryptedSource(tmp_path / "same", tmp_path / "same", None, _descriptor())


def test_mode_off_touches_nothing(tmp_path):
    from acquisition import AcquisitionCoordinator, AcquisitionSourceSet, AcquisitionState

    class BombStore:
        def load(self, descriptor):
            raise AssertionError("key store touched")

    root = tmp_path / "workspaces"
    source_set = AcquisitionSourceSet((_source(tmp_path),))
    coordinator = AcquisitionCoordinator(BombStore(), root, decryptor=FakeDecryptor())
    with coordinator.prepare(source_set, database_mode_enabled=False) as outcome:
        assert outcome.readiness.state is AcquisitionState.DISABLED
        assert outcome.prepared_source is None
    assert not root.exists()


def test_missing_any_required_key_needs_bootstrap_without_workspace(tmp_path):
    from acquisition import AcquisitionState, SecretBytes

    first = _source(tmp_path, "one", _descriptor("a"))
    second = _source(tmp_path, "two", _descriptor("c"))
    coordinator, source_set, store = _coordinator(
        tmp_path, (first, second), keys={first.key_descriptor: SecretBytes(b"k" * 32)}
    )
    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        assert outcome.readiness.state is AcquisitionState.NEEDS_BOOTSTRAP
        assert outcome.prepared_source is None
    assert store.loads == [first.key_descriptor, second.key_descriptor]
    assert not (tmp_path / "workspaces").exists()


def test_ready_lease_preserves_roles_and_removes_workspaces_on_exit(tmp_path):
    from acquisition import AcquisitionSourceSet, AcquisitionState

    messages = (_source(tmp_path, "m1", _descriptor("a")),
                _source(tmp_path, "m2", _descriptor("c")))
    conversation = _source(tmp_path, "conversation", _descriptor("d"))
    display = _source(tmp_path, "display", _descriptor("e"))
    all_sources = messages + (conversation, display)
    coordinator, _, _ = _coordinator(tmp_path, all_sources)
    source_set = AcquisitionSourceSet(messages, conversation, display)
    root = tmp_path / "workspaces"

    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        assert outcome.readiness.state is AcquisitionState.READY
        prepared = outcome.prepared_source
        assert prepared is not None
        paths = tuple(handle.value for handle in prepared.message_handles)
        assert len(paths) == 2 and all(path.exists() for path in paths)
        assert prepared.conversation_identity_handle.value.exists()
        assert prepared.display_identity_handle.value.exists()
        assert [call[0].read_bytes() for call in coordinator._decryptor.calls] == [
            b"m1", b"m2", b"conversation", b"display",
        ]
        assert [path.name for path in paths] != ["m1", "m2"]
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths)
        assert root.stat().st_mode & 0o777 == 0o700
    assert root.exists() and list(root.iterdir()) == []


@pytest.mark.parametrize(
    ("conversation_present", "display_present"),
    [(True, False), (False, True)],
)
def test_identity_role_handles_are_independently_optional(
    tmp_path, conversation_present, display_present
):
    from acquisition import AcquisitionSourceSet, AcquisitionState

    message = _source(tmp_path, "message", _descriptor("a"))
    conversation = (
        _source(tmp_path, "conversation", _descriptor("c"))
        if conversation_present else None
    )
    display = (
        _source(tmp_path, "display", _descriptor("d"))
        if display_present else None
    )
    sources = tuple(source for source in (message, conversation, display) if source)
    coordinator, _, _ = _coordinator(tmp_path, sources)
    source_set = AcquisitionSourceSet((message,), conversation, display)

    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        assert outcome.readiness.state is AcquisitionState.READY
        assert (outcome.prepared_source.conversation_identity_handle is not None) \
            is conversation_present
        assert (outcome.prepared_source.display_identity_handle is not None) \
            is display_present


def test_workspace_is_removed_when_consumer_raises(tmp_path):
    coordinator, source_set, _ = _coordinator(tmp_path, (_source(tmp_path),))
    root = tmp_path / "workspaces"
    with pytest.raises(RuntimeError, match="consumer"):
        with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
            assert outcome.prepared_source.message_handles[0].value.exists()
            raise RuntimeError("consumer")
    assert list(root.iterdir()) == []


def test_cleanup_failure_is_reported_instead_of_leaving_a_successful_lease(
    monkeypatch, tmp_path
):
    import acquisition.coordinator as coordinator_module
    from acquisition.coordinator import AcquisitionCleanupError

    coordinator, source_set, _ = _coordinator(tmp_path, (_source(tmp_path),))
    root = tmp_path / "workspaces"
    with pytest.raises(AcquisitionCleanupError, match="^acquisition cleanup failed$"):
        with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
            assert outcome.prepared_source is not None
            monkeypatch.setattr(
                coordinator_module.shutil, "rmtree",
                lambda *args, **kwargs: (_ for _ in ()).throw(OSError(5, "fixture")),
            )
    assert any(root.iterdir())


def test_bad_sqlite_and_decrypt_failure_publish_no_prepared_source(tmp_path):
    from acquisition import AcquisitionState
    from acquisition.decryptor import DatabaseDecryptError

    for decryptor in (
        FakeDecryptor(valid=False),
        FakeDecryptor(error=DatabaseDecryptError("database decrypt failed")),
    ):
        coordinator, source_set, _ = _coordinator(
            tmp_path, (_source(tmp_path, os.urandom(4).hex()),), decryptor=decryptor
        )
        with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
            assert outcome.readiness.state is AcquisitionState.DECRYPT_FAILED
            assert outcome.prepared_source is None
        assert list((tmp_path / "workspaces").iterdir()) == []


def test_wrong_stored_key_maps_to_needs_bootstrap(tmp_path):
    from acquisition import AcquisitionState
    from acquisition.decryptor import DatabaseKeyError

    coordinator, source_set, _ = _coordinator(
        tmp_path,
        (_source(tmp_path),),
        decryptor=FakeDecryptor(error=DatabaseKeyError("database key invalid")),
    )
    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        assert outcome.readiness.state is AcquisitionState.NEEDS_BOOTSTRAP
        assert outcome.prepared_source is None


@pytest.mark.parametrize(
    ("error", "expected_state"),
    [
        ("source busy", "SOURCE_BUSY"),
        ("snapshot unstable", "SNAPSHOT_UNSTABLE"),
        ("snapshot format unsupported", "VERSION_UNVERIFIED"),
        ("unexpected", "INTERNAL_ERROR"),
    ],
)
def test_snapshot_failures_map_to_closed_readiness_without_decrypting(
    tmp_path, error, expected_state
):
    from acquisition import AcquisitionCoordinator, AcquisitionSourceSet, AcquisitionState
    from acquisition.snapshot import SnapshotError, SnapshotFormatError

    class FailingSnapshotter:
        def copy(self, sources, destination):
            if error == "snapshot format unsupported":
                raise SnapshotFormatError()
            if error == "unexpected":
                raise RuntimeError("private detail")
            raise SnapshotError(error)

    source = _source(tmp_path)
    decryptor = FakeDecryptor()
    _, source_set, store = _coordinator(tmp_path, (source,))
    coordinator = AcquisitionCoordinator(
        store, tmp_path / "workspaces",
        snapshotter=FailingSnapshotter(), decryptor=decryptor,
    )

    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        assert outcome.readiness.state is getattr(AcquisitionState, expected_state)
        assert outcome.prepared_source is None
    assert decryptor.calls == []
    assert list((tmp_path / "workspaces").iterdir()) == []


def test_snapshot_destination_failure_maps_to_internal_error(tmp_path):
    from acquisition import AcquisitionCoordinator, AcquisitionState
    from acquisition.snapshot import Snapshotter

    source = _source(tmp_path)
    _, source_set, store = _coordinator(tmp_path, (source,))

    def destination_full(src, dst):
        raise OSError(28, "fixture workspace full", dst)

    coordinator = AcquisitionCoordinator(
        store, tmp_path / "workspaces",
        snapshotter=Snapshotter(attempts=1, copy_file=destination_full),
        decryptor=FakeDecryptor(),
    )
    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        assert outcome.readiness.state is AcquisitionState.INTERNAL_ERROR
        assert outcome.prepared_source is None


def test_private_wal_io_failure_maps_to_internal_error(tmp_path):
    from acquisition import AcquisitionCoordinator, AcquisitionState
    from acquisition.snapshot import EncryptedSnapshot

    source = _source(tmp_path)
    _, source_set, store = _coordinator(tmp_path, (source,))
    unreadable_wal = tmp_path / "wal-directory"
    unreadable_wal.mkdir()

    class SnapshotWithUnreadableWal:
        def copy(self, sources, destination):
            return (EncryptedSnapshot(
                source.main, unreadable_wal, None, source.key_descriptor
            ),)

    coordinator = AcquisitionCoordinator(
        store, tmp_path / "workspaces",
        snapshotter=SnapshotWithUnreadableWal(), decryptor=FakeDecryptor(),
    )
    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        assert outcome.readiness.state is AcquisitionState.INTERNAL_ERROR
        assert outcome.prepared_source is None


def test_schema_validation_failure_closes_the_connection(monkeypatch, tmp_path):
    from acquisition.coordinator import _valid_sqlite

    class FailingConnection:
        closed = False

        def execute(self, statement):
            raise sqlite3.DatabaseError("fixture detail")

        def close(self):
            self.closed = True

    connection = FailingConnection()
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: connection)

    assert _valid_sqlite(tmp_path / "plain") is False
    assert connection.closed is True


def test_janitor_is_bounded_and_touches_only_stale_owned_children(tmp_path):
    from acquisition.coordinator import cleanup_stale_workspaces

    root = tmp_path / "root"
    root.mkdir()
    stale = root / "acq-stale"
    fresh = root / "acq-fresh"
    foreign = root / "foreign"
    for path in (stale, fresh, foreign):
        path.mkdir()
        (path / "evidence").write_text("keep-or-remove")
    old = time.time() - 1000
    os.utime(stale, (old, old))
    cleanup_stale_workspaces(root, older_than_seconds=100, now=time.time(), limit=1)
    assert not stale.exists()
    assert fresh.exists() and foreign.exists()


def test_fast_lane_architecture_stays_isolated_and_existing_seals_are_unchanged():
    forbidden = {
        "bridge", "memory", "wechatdb", "subprocess", "requests", "socket",
        "urllib", "frida", "bootstrap", "ocr",
    }
    for name in ("snapshot.py", "decryptor.py", "coordinator.py"):
        source = (ROOT / "acquisition" / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        roots = {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        roots |= {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert roots.isdisjoint(forbidden)
        assert "core.decryptor" not in source and "core.wechat_db" not in source
