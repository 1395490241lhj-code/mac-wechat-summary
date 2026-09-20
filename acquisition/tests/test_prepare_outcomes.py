"""prepare() classifies acquisition failures, and only acquisition failures.

The contextmanager has three phases and they must not bleed into each other:
failures produced *before* the lease exists may become a typed acquisition
outcome; anything raised by the consumer *after* the lease is yielded is the
consumer's and must propagate untouched; and cleanup runs in every case.

The regression these tests pin: an acquisition-side failure used to be reported
through a yield that sat inside the same except block that produced it, so a
consumer exception thrown back into the generator was caught as though
acquisition had failed and the generator yielded a second time -- Python then
raised RuntimeError("generator didn't stop after throw()") and the real
classification (needs_bootstrap, snapshot_unstable, ...) was lost.
"""

from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "bridge"):
    sys.path.insert(0, str(path))

from acquisition import (  # noqa: E402
    AcquisitionCoordinator,
    AcquisitionSourceSet,
    AcquisitionState,
    EncryptedSource,
    KeyDescriptor,
    SecretBytes,
)

FINGERPRINT = "a" * 64
COMPATIBILITY = "b" * 64


class _ConsumerError(Exception):
    """Raised by a consumer inside the lease, to prove it is not swallowed."""


class _Store:
    def __init__(self, secret: bytes | None) -> None:
        self._secret = secret

    def load(self, descriptor: KeyDescriptor) -> SecretBytes | None:
        return SecretBytes(self._secret) if self._secret is not None else None


class _BrokenStore:
    def load(self, descriptor: KeyDescriptor):
        raise OSError("synthetic key store failure")


class _Decryptor:
    def decrypt(self, source: Path, output: Path, secret: SecretBytes) -> None:
        shutil.copyfile(source, output)


def _sqlite(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE fixture(value INTEGER)")
    connection.commit()
    connection.close()


def _source_set(tmp_path: Path) -> AcquisitionSourceSet:
    main = tmp_path / "source.sqlite"
    _sqlite(main)
    return AcquisitionSourceSet((
        EncryptedSource(
            main, None, None,
            KeyDescriptor(FINGERPRINT, "messages", 1, COMPATIBILITY)),
    ))


def _coordinator(tmp_path: Path, *, store=None) -> AcquisitionCoordinator:
    return AcquisitionCoordinator(
        store if store is not None else _Store(b"synthetic-secret"),
        tmp_path / "leases",
        decryptor=_Decryptor(),
    )


def test_missing_key_material_is_classified_as_needs_bootstrap(tmp_path):
    coordinator = _coordinator(tmp_path, store=_Store(None))

    with coordinator.prepare(
        _source_set(tmp_path), database_mode_enabled=True
    ) as outcome:
        assert outcome.readiness.state is AcquisitionState.NEEDS_BOOTSTRAP
        assert outcome.prepared_source is None


def test_a_consumer_failure_after_a_non_ready_outcome_propagates_unchanged(tmp_path):
    coordinator = _coordinator(tmp_path, store=_Store(None))

    with pytest.raises(_ConsumerError):
        with coordinator.prepare(
            _source_set(tmp_path), database_mode_enabled=True
        ) as outcome:
            assert outcome.readiness.state is AcquisitionState.NEEDS_BOOTSTRAP
            raise _ConsumerError()


def test_a_consumer_failure_after_an_internal_failure_propagates_unchanged(tmp_path):
    coordinator = _coordinator(tmp_path, store=_BrokenStore())

    with pytest.raises(_ConsumerError):
        with coordinator.prepare(
            _source_set(tmp_path), database_mode_enabled=True
        ) as outcome:
            assert outcome.readiness.state is AcquisitionState.INTERNAL_ERROR
            raise _ConsumerError()


def test_a_consumer_failure_after_a_ready_outcome_propagates_unchanged(tmp_path):
    coordinator = _coordinator(tmp_path)

    with pytest.raises(_ConsumerError):
        with coordinator.prepare(
            _source_set(tmp_path), database_mode_enabled=True
        ) as outcome:
            assert outcome.readiness.state is AcquisitionState.READY
            raise _ConsumerError()


def test_cleanup_still_runs_when_the_consumer_raises(tmp_path):
    coordinator = _coordinator(tmp_path)
    root = tmp_path / "leases"

    with pytest.raises(_ConsumerError):
        with coordinator.prepare(
            _source_set(tmp_path), database_mode_enabled=True
        ) as outcome:
            assert outcome.readiness.state is AcquisitionState.READY
            raise _ConsumerError()

    assert root.exists() and list(root.iterdir()) == []


def test_cleanup_still_runs_on_cancellation(tmp_path):
    coordinator = _coordinator(tmp_path)
    root = tmp_path / "leases"

    with pytest.raises(KeyboardInterrupt):
        with coordinator.prepare(
            _source_set(tmp_path), database_mode_enabled=True
        ) as outcome:
            assert outcome.readiness.state is AcquisitionState.READY
            raise KeyboardInterrupt

    assert root.exists() and list(root.iterdir()) == []


def test_cleanup_still_runs_when_the_consumer_completes(tmp_path):
    coordinator = _coordinator(tmp_path)
    root = tmp_path / "leases"

    with coordinator.prepare(
        _source_set(tmp_path), database_mode_enabled=True
    ) as outcome:
        assert outcome.readiness.state is AcquisitionState.READY

    assert root.exists() and list(root.iterdir()) == []

