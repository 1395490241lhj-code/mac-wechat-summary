"""Bootstrap: the explicit lifecycle composition root for database mode.

Bootstrap is not part of reading. It exists for exactly two lifecycle events --
first database enable, and repair after a source or key has been invalidated --
and it is invoked explicitly, never by an ordinary read and never on launch.

This is the one module besides the ordinary adapter that may coordinate both the
acquisition layer and the schema provider, because establishing a usable
configuration requires proving a candidate secret *and* a candidate schema
before either becomes active.

Nothing here extracts a key. The secret arrives from an injected
BootstrapSecretProvider, and the only implementation shipped is the operator
entering it locally. Automatic process-memory acquisition stays deferred behind
its own review; the v1 scanner is neither imported nor invoked.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

try:
    from acquisition import (
        AcquisitionCoordinator,
        AcquisitionSourceSet,
        AcquisitionState,
        EncryptedSource,
        KeyDescriptor,
        KeyStore,
        SecretBytes,
        SourceLocator,
    )
    from acquisition.descriptor import (
        DescriptorEvidenceError,
        descriptors_for,
        source_fingerprint,
    )
    from acquisition.source_locator import (
        ROLE_CONVERSATION_IDENTITY,
        ROLE_DISPLAY_IDENTITY,
        ROLE_MESSAGES,
        record_document,
    )
except ImportError:  # pragma: no cover - launched by path from another cwd
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from acquisition import (
        AcquisitionCoordinator,
        AcquisitionSourceSet,
        AcquisitionState,
        EncryptedSource,
        KeyDescriptor,
        KeyStore,
        SecretBytes,
        SourceLocator,
    )
    from acquisition.descriptor import (
        DescriptorEvidenceError,
        descriptors_for,
        source_fingerprint,
    )
    from acquisition.source_locator import (
        ROLE_CONVERSATION_IDENTITY,
        ROLE_DISPLAY_IDENTITY,
        ROLE_MESSAGES,
        record_document,
    )

from message_source import SOURCE_DATABASE, SOURCE_VISUAL  # noqa: F401
from wechatdb.provider import (
    ExplicitShardLocator,
    IdentityCatalog,
    ReadOnlySqliteOpener,
    ShardEntry,
    ShardedMessageProvider,
    UnsupportedGeneration,
)

# --- Result vocabulary -------------------------------------------------------
#
# Fixed, content-free tokens. None is assembled from the failure it describes:
# a secret, a path, a table name or a chat identifier must not reach a caller
# through an error string.

BOOTSTRAP_READY: str = "ready"
BOOTSTRAP_SOURCE_MISSING: str = "source_missing"
BOOTSTRAP_SOURCE_INVALID: str = "source_invalid"
BOOTSTRAP_SECRET_NOT_SUPPLIED: str = "secret_not_supplied"
BOOTSTRAP_SECRET_REJECTED: str = "secret_rejected"
BOOTSTRAP_SOURCE_CHANGED: str = "source_changed"
BOOTSTRAP_UNSUPPORTED_GENERATION: str = "unsupported_generation"
BOOTSTRAP_DURABLE_WRITE_FAILED: str = "durable_write_failed"
BOOTSTRAP_VERIFICATION_FAILED: str = "verification_failed"

BOOTSTRAP_STATES: frozenset[str] = frozenset({
    BOOTSTRAP_READY,
    BOOTSTRAP_SOURCE_MISSING,
    BOOTSTRAP_SOURCE_INVALID,
    BOOTSTRAP_SECRET_NOT_SUPPLIED,
    BOOTSTRAP_SECRET_REJECTED,
    BOOTSTRAP_SOURCE_CHANGED,
    BOOTSTRAP_UNSUPPORTED_GENERATION,
    BOOTSTRAP_DURABLE_WRITE_FAILED,
    BOOTSTRAP_VERIFICATION_FAILED,
})

#: How an acquisition outcome becomes a Bootstrap state. Every acquisition state
#: is mapped, so a new one cannot silently become a success.
_ACQUISITION_STATES: dict[AcquisitionState, str] = {
    AcquisitionState.DISABLED: BOOTSTRAP_VERIFICATION_FAILED,
    AcquisitionState.NEEDS_BOOTSTRAP: BOOTSTRAP_SECRET_REJECTED,
    AcquisitionState.SOURCE_BUSY: BOOTSTRAP_SOURCE_CHANGED,
    AcquisitionState.SNAPSHOT_UNSTABLE: BOOTSTRAP_SOURCE_CHANGED,
    AcquisitionState.DECRYPT_FAILED: BOOTSTRAP_SECRET_REJECTED,
    AcquisitionState.SCHEMA_UNSUPPORTED: BOOTSTRAP_UNSUPPORTED_GENERATION,
    AcquisitionState.VERSION_UNVERIFIED: BOOTSTRAP_UNSUPPORTED_GENERATION,
    AcquisitionState.INTERNAL_ERROR: BOOTSTRAP_VERIFICATION_FAILED,
}

#: The source layout inside one explicitly selected root.
MESSAGE_DIRECTORY: str = "message"
SESSION_DIRECTORY: str = "session"
CONTACT_DIRECTORY: str = "contact"
SESSION_FILE: str = "session.db"
CONTACT_FILE: str = "contact.db"


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """The outcome of one explicit Bootstrap attempt. Never carries a secret."""

    state: str

    def __post_init__(self) -> None:
        if self.state not in BOOTSTRAP_STATES:
            raise ValueError("bootstrap state invalid")

    @property
    def ready(self) -> bool:
        return self.state == BOOTSTRAP_READY


class BootstrapSourceError(Exception):
    """The explicitly supplied source root is missing or structurally invalid."""

    def __init__(self, state: str) -> None:
        if state not in {BOOTSTRAP_SOURCE_MISSING, BOOTSTRAP_SOURCE_INVALID}:
            raise ValueError("bootstrap source reason invalid")
        super().__init__(state)


class BootstrapSecretProvider(Protocol):
    """How Bootstrap obtains a candidate account secret.

    One account-level candidate is requested for the whole source instance. The
    boundary exists so a future, separately reviewed acquisition helper can
    implement it without changing any of the orchestration below.
    """

    def acquire(self, fingerprint: str) -> SecretBytes | None:
        """The candidate secret, or None when the operator supplied nothing."""


@dataclass(frozen=True, slots=True)
class OperatorSuppliedSecret:
    """An operator entering the secret locally.

    The value is read through a callable so the mechanism stays outside this
    module: the command line wires it to a non-echoing prompt. It is never an
    argument, never an environment variable, never written anywhere, and it
    becomes SecretBytes immediately.
    """

    read: Callable[[], bytes | None]

    def acquire(self, fingerprint: str) -> SecretBytes | None:
        value = self.read()
        if not value:
            return None
        return SecretBytes(value)


class _CandidateSecrets:
    """The candidate secret, offered for validation only.

    Acquisition consumes exactly one operation -- look a descriptor up -- so an
    in-memory lookup that answers from the candidate lets the real Fast Lane
    validate it without the candidate ever reaching durable storage. Nothing is
    written until validation has already succeeded.
    """

    def __init__(self, descriptors: tuple[KeyDescriptor, ...], secret: SecretBytes) -> None:
        self._descriptors = frozenset(descriptors)
        self._secret = secret

    def load(self, descriptor: KeyDescriptor) -> SecretBytes | None:
        return self._secret if descriptor in self._descriptors else None


def candidate_source_set(source_root: Path) -> AcquisitionSourceSet:
    """The one explicitly selected root, as the acquisition contract wants it.

    The root is supplied, never chosen: no scoring, no newest-account rule, no
    recursive search. The two identity anchors must carry encrypted salt
    evidence, and at least one message part must exist.
    """
    root = Path(source_root)
    if not root.is_dir():
        raise BootstrapSourceError(BOOTSTRAP_SOURCE_MISSING)
    session = root / SESSION_DIRECTORY / SESSION_FILE
    contact = root / CONTACT_DIRECTORY / CONTACT_FILE
    try:
        fingerprint = source_fingerprint(session, contact)
    except DescriptorEvidenceError:
        raise BootstrapSourceError(BOOTSTRAP_SOURCE_INVALID) from None
    descriptors = descriptors_for(fingerprint)
    directory = root / MESSAGE_DIRECTORY
    parts = tuple(sorted(directory.glob("*.db"))) if directory.is_dir() else ()
    if not parts:
        raise BootstrapSourceError(BOOTSTRAP_SOURCE_INVALID)
    return AcquisitionSourceSet(
        tuple(
            _encrypted_source(path, descriptors[ROLE_MESSAGES]) for path in parts
        ),
        _encrypted_source(session, descriptors[ROLE_CONVERSATION_IDENTITY]),
        _encrypted_source(contact, descriptors[ROLE_DISPLAY_IDENTITY]),
    )


def _encrypted_source(main: Path, descriptor: KeyDescriptor) -> EncryptedSource:
    wal = main.with_name(main.name + "-wal")
    shm = main.with_name(main.name + "-shm")
    return EncryptedSource(
        main, wal if wal.is_file() else None, shm if shm.is_file() else None, descriptor
    )


def _validate(
    source_set: AcquisitionSourceSet,
    secret: SecretBytes,
    *,
    workspace_root: Path,
    snapshotter: object | None,
    decryptor: object | None,
) -> str:
    """Prove the candidate against every required role, then the provider gate.

    The whole of this is the existing Fast Lane and the existing provider: a
    second decryption path or a second compatibility rule would be a second
    thing to keep correct, and a place for the two to disagree.
    """
    descriptors = tuple(
        source.key_descriptor for source in source_set.ordered_sources()
    )
    coordinator = AcquisitionCoordinator(
        _CandidateSecrets(descriptors, secret),
        workspace_root,
        snapshotter=snapshotter,
        decryptor=decryptor,
    )
    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        if outcome.readiness.state is not AcquisitionState.READY:
            return _ACQUISITION_STATES[outcome.readiness.state]
        prepared = outcome.prepared_source
        assert prepared is not None
        entries = tuple(
            ShardEntry("message_{}.db".format(index), handle.value)
            for index, handle in enumerate(prepared.message_handles)
        )
        opener = ReadOnlySqliteOpener()
        catalog = IdentityCatalog.build(
            opener=opener,
            session=(
                ShardEntry("session.db", prepared.conversation_identity_handle.value)
                if prepared.conversation_identity_handle else None
            ),
            contact=(
                ShardEntry("contact.db", prepared.display_identity_handle.value)
                if prepared.display_identity_handle else None
            ),
            message_parts=entries,
        )
        provider = ShardedMessageProvider(
            ExplicitShardLocator(entries), identities=catalog.resolver()
        )
        try:
            provider.list_conversations(1)
        except UnsupportedGeneration:
            return BOOTSTRAP_UNSUPPORTED_GENERATION
        except Exception:
            return BOOTSTRAP_VERIFICATION_FAILED
        if provider.diagnostics.unknown or provider.diagnostics.unavailable:
            return BOOTSTRAP_UNSUPPORTED_GENERATION
    return BOOTSTRAP_READY


def _write_record(manifest_path: Path, document: dict[str, object]) -> None:
    """Publish the activation pointer, atomically.

    Written to a sibling and renamed, so a reader never observes a half-written
    record: the rename is the point at which the new decision exists.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(document, sort_keys=True).encode("utf-8")
    handle, temporary = tempfile.mkstemp(
        dir=str(manifest_path.parent), prefix=".record-"
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
        os.chmod(temporary, 0o600)
        os.replace(temporary, manifest_path)
    except OSError:
        Path(temporary).unlink(missing_ok=True)
        raise


def _publish(
    source_set: AcquisitionSourceSet,
    secret: SecretBytes,
    *,
    key_store: object,
    manifest_path: Path,
    verify: Callable[[], bool],
) -> str | None:
    """Write the key entries, then publish the record; compensate on failure.

    The record is the activation pointer and is written last, so there is no
    instant at which an active record points at key material that was never
    written. If publication fails, every entry this call changed is put back --
    a previously valid configuration survives a failed refresh.

    Returns None on success, or the fixed state that must be reported after the
    previous durable state has been restored.
    """
    descriptors = tuple(
        source.key_descriptor for source in source_set.ordered_sources()
    )
    previous = {descriptor: key_store.load(descriptor) for descriptor in descriptors}
    existing_record = manifest_path.read_bytes() if manifest_path.is_file() else None
    written: list[KeyDescriptor] = []
    failure: str | None = None
    try:
        for descriptor in descriptors:
            key_store.put(descriptor, secret)
            written.append(descriptor)
        _write_record(manifest_path, record_document(source_set))
        if not verify():
            failure = BOOTSTRAP_VERIFICATION_FAILED
    except Exception:
        failure = BOOTSTRAP_DURABLE_WRITE_FAILED
    if failure is None:
        return None
    for descriptor in written:
        prior = previous[descriptor]
        try:
            if prior is None:
                key_store.delete(descriptor)
            else:
                key_store.put(descriptor, prior)
        except Exception:
            continue
    try:
        if existing_record is None:
            manifest_path.unlink(missing_ok=True)
        else:
            _write_record(manifest_path, json.loads(existing_record.decode("utf-8")))
    except Exception:
        pass
    return failure


def bootstrap(
    source_root: Path,
    *,
    secret_provider: BootstrapSecretProvider,
    key_store: object,
    manifest_path: Path,
    workspace_root: Path,
    snapshotter: object | None = None,
    decryptor: object | None = None,
    verify: Callable[[], bool] | None = None,
) -> BootstrapResult:
    """Establish, or explicitly refresh, the durable source/key pair.

    The candidate is validated completely before anything durable changes, so a
    failed attempt publishes no active record and no new key state, and a failed
    refresh leaves the previous known-good pair exactly as it was.
    """
    try:
        source_set = candidate_source_set(source_root)
    except BootstrapSourceError as error:
        return BootstrapResult(str(error))

    fingerprint = source_set.message_sources[0].key_descriptor.source_fingerprint
    secret = secret_provider.acquire(fingerprint)
    if secret is None:
        return BootstrapResult(BOOTSTRAP_SECRET_NOT_SUPPLIED)

    state = _validate(
        source_set, secret,
        workspace_root=workspace_root,
        snapshotter=snapshotter,
        decryptor=decryptor,
    )
    if state != BOOTSTRAP_READY:
        return BootstrapResult(state)

    def durable_pair_reopens() -> bool:
        return SourceLocator(manifest_path).resolve().source_set is not None

    failure = _publish(
        source_set, secret,
        key_store=key_store,
        manifest_path=manifest_path,
        verify=verify if verify is not None else durable_pair_reopens,
    )
    return BootstrapResult(failure if failure is not None else BOOTSTRAP_READY)


def main(argv: list[str] | None = None) -> int:
    """The explicit operator entry point.

    The source root is an argument because it is a location, not a secret. The
    secret is prompted for locally without echo: never argv, never an
    environment variable, never shell history.
    """
    import argparse

    from acquired_database_source import (
        acquisition_workspace_root,
        recorded_manifest_path,
    )

    parser = argparse.ArgumentParser(
        prog="wechat-database-bootstrap",
        description=(
            "Explicitly establish or refresh database mode for one source root. "
            "The secret is entered locally and is never written anywhere."
        ),
    )
    parser.add_argument("--source-root", required=True, type=Path)
    arguments = parser.parse_args(argv)

    def read() -> bytes | None:
        entered = getpass.getpass("database key (input hidden): ")
        return entered.encode("utf-8") if entered else None

    result = bootstrap(
        arguments.source_root,
        secret_provider=OperatorSuppliedSecret(read),
        key_store=KeyStore(),
        manifest_path=recorded_manifest_path(),
        workspace_root=acquisition_workspace_root(),
    )
    print(json.dumps({"state": result.state}), flush=True)
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
