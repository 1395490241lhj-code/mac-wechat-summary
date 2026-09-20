"""P2-A source-neutral acquisition contracts, with no acquisition runtime."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
import importlib
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "acquisition" / "contracts.py"
PACKAGE = ROOT / "acquisition" / "__init__.py"


def test_readiness_vocabulary_is_closed_and_exact():
    from acquisition import AcquisitionState

    assert [(state.name, state.value) for state in AcquisitionState] == [
        ("DISABLED", "disabled"),
        ("NEEDS_BOOTSTRAP", "needs_bootstrap"),
        ("READY", "ready"),
        ("SOURCE_BUSY", "source_busy"),
        ("SNAPSHOT_UNSTABLE", "snapshot_unstable"),
        ("DECRYPT_FAILED", "decrypt_failed"),
        ("SCHEMA_UNSUPPORTED", "schema_unsupported"),
        ("VERSION_UNVERIFIED", "version_unverified"),
        ("INTERNAL_ERROR", "internal_error"),
    ]


def test_readiness_stores_only_state_and_derives_database_mode():
    from acquisition import AcquisitionReadiness, AcquisitionState

    disabled = AcquisitionReadiness(AcquisitionState.DISABLED)
    ready = AcquisitionReadiness(AcquisitionState.READY)
    bootstrap = AcquisitionReadiness(AcquisitionState.NEEDS_BOOTSTRAP)

    assert [field.name for field in fields(AcquisitionReadiness)] == ["state"]
    assert disabled.database_mode_enabled is False
    assert ready.database_mode_enabled is True
    assert bootstrap.database_mode_enabled is True


def test_every_active_failure_state_derives_database_mode_enabled():
    from acquisition import AcquisitionReadiness, AcquisitionState

    active_failures = (
        AcquisitionState.SOURCE_BUSY,
        AcquisitionState.SNAPSHOT_UNSTABLE,
        AcquisitionState.DECRYPT_FAILED,
        AcquisitionState.SCHEMA_UNSUPPORTED,
        AcquisitionState.VERSION_UNVERIFIED,
        AcquisitionState.INTERNAL_ERROR,
    )

    assert all(AcquisitionReadiness(state).database_mode_enabled
               for state in active_failures)


def test_readiness_refuses_values_outside_the_closed_vocabulary():
    from acquisition import AcquisitionReadiness

    secret = "fixture_secret_state"
    with pytest.raises(ValueError, match="^acquisition state invalid$") as refusal:
        AcquisitionReadiness(secret)
    assert secret not in str(refusal.value)


def test_readiness_is_immutable():
    from acquisition import AcquisitionReadiness, AcquisitionState

    readiness = AcquisitionReadiness(AcquisitionState.DISABLED)
    with pytest.raises(FrozenInstanceError):
        readiness.state = AcquisitionState.READY


def test_prepared_source_snapshots_messages_and_allows_missing_identity_roles():
    from acquisition import OpaqueHandle, PreparedSource

    first = OpaqueHandle(object())
    second = OpaqueHandle(object())
    caller_owned = [first]

    prepared = PreparedSource(message_handles=caller_owned)
    caller_owned.append(second)

    assert prepared.message_handles == (first,)
    assert prepared.conversation_identity_handle is None
    assert prepared.display_identity_handle is None
    assert isinstance(prepared.message_handles, tuple)


def test_prepared_source_preserves_explicit_identity_role_handles():
    from acquisition import OpaqueHandle, PreparedSource

    messages = (OpaqueHandle(object()), OpaqueHandle(object()))
    conversation_identity = OpaqueHandle(object())
    display_identity = OpaqueHandle(object())

    prepared = PreparedSource(
        message_handles=messages,
        conversation_identity_handle=conversation_identity,
        display_identity_handle=display_identity,
    )

    assert prepared.message_handles == messages
    assert prepared.conversation_identity_handle is conversation_identity
    assert prepared.display_identity_handle is display_identity


def test_prepared_source_requires_at_least_one_valid_message_handle():
    from acquisition import PreparedSource

    secret = "fixture_secret_handle"
    for invalid in ((), [], [secret], None):
        with pytest.raises(ValueError, match="^prepared source invalid$") as refusal:
            PreparedSource(message_handles=invalid)
        assert secret not in str(refusal.value)


def test_prepared_source_and_opaque_handles_are_immutable_and_content_free_in_repr():
    from acquisition import OpaqueHandle, PreparedSource

    secret = "fixture_secret_opaque_value"
    handle = OpaqueHandle(secret)
    prepared = PreparedSource(message_handles=(handle,))

    assert secret not in repr(handle) and secret not in repr(prepared)
    with pytest.raises(FrozenInstanceError):
        handle.value = object()
    with pytest.raises(FrozenInstanceError):
        prepared.message_handles = ()


def test_prepared_source_public_payload_has_only_opaque_source_roles():
    from acquisition import OpaqueHandle, PreparedSource

    assert [field.name for field in fields(OpaqueHandle)] == ["value"]
    assert [field.name for field in fields(PreparedSource)] == [
        "message_handles",
        "conversation_identity_handle",
        "display_identity_handle",
    ]
    public_fields = {
        field.name
        for contract in (OpaqueHandle, PreparedSource)
        for field in fields(contract)
    }
    for forbidden in ("key", "path", "user", "content", "salt", "log", "readiness"):
        assert all(forbidden not in name.lower() for name in public_fields)


def test_ready_requires_a_prepared_source_and_accepts_one():
    from acquisition import (
        AcquisitionOutcome,
        AcquisitionReadiness,
        AcquisitionState,
        OpaqueHandle,
        PreparedSource,
    )

    readiness = AcquisitionReadiness(AcquisitionState.READY)
    with pytest.raises(ValueError, match="^acquisition outcome contradiction$"):
        AcquisitionOutcome(readiness)

    prepared = PreparedSource(message_handles=(OpaqueHandle(object()),))
    outcome = AcquisitionOutcome(readiness, prepared)
    assert outcome.readiness is readiness and outcome.prepared_source is prepared


@pytest.mark.parametrize("state", [
    "DISABLED",
    "NEEDS_BOOTSTRAP",
    "SOURCE_BUSY",
    "SNAPSHOT_UNSTABLE",
    "DECRYPT_FAILED",
    "SCHEMA_UNSUPPORTED",
    "VERSION_UNVERIFIED",
    "INTERNAL_ERROR",
])
def test_every_non_ready_state_forbids_a_prepared_source(state):
    from acquisition import (
        AcquisitionOutcome,
        AcquisitionReadiness,
        AcquisitionState,
        OpaqueHandle,
        PreparedSource,
    )

    readiness = AcquisitionReadiness(getattr(AcquisitionState, state))
    prepared = PreparedSource(message_handles=(OpaqueHandle(object()),))

    assert AcquisitionOutcome(readiness).prepared_source is None
    with pytest.raises(ValueError, match="^acquisition outcome contradiction$"):
        AcquisitionOutcome(readiness, prepared)


def test_outcome_is_immutable_and_carries_exactly_readiness_plus_success():
    from acquisition import AcquisitionOutcome, AcquisitionReadiness, AcquisitionState

    outcome = AcquisitionOutcome(AcquisitionReadiness(AcquisitionState.DISABLED))

    assert [field.name for field in fields(AcquisitionOutcome)] == [
        "readiness", "prepared_source"]
    with pytest.raises(FrozenInstanceError):
        outcome.prepared_source = object()


def test_outcome_contract_violations_use_fixed_content_free_copy():
    from acquisition import AcquisitionOutcome

    secret = "fixture_secret_outcome"
    with pytest.raises(ValueError, match="^acquisition outcome contradiction$") as refusal:
        AcquisitionOutcome(secret)
    assert secret not in str(refusal.value)


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _identifiers(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
    return names


def test_contract_owner_imports_only_contract_standard_library():
    assert _imported_roots(CONTRACTS) == {"__future__", "dataclasses", "enum"}


def test_contract_has_no_reader_coverage_or_provider_schema_vocabulary():
    source = CONTRACTS.read_text(encoding="utf-8")
    names = _identifiers(CONTRACTS)

    assert names.isdisjoint({
        "ReadCoverage", "ReadFreshness", "ReadResult", "COVERAGE_REASONS",
        "ShardEntry", "IdentityCatalog", "ShardedMessageProvider",
        "conversation_identifier",
    })
    for forbidden in (
        "Msg_", "Name2Id", "SessionTable", "contact", "local_id",
        "server_id", "real_sender_id", "WeChat SQL",
    ):
        assert forbidden not in source


def test_contract_has_no_acquisition_implementation_capability():
    roots = _imported_roots(CONTRACTS)
    names = _identifiers(CONTRACTS)

    assert roots.isdisjoint({
        "Crypto", "Security", "cryptography", "ctypes", "frida", "glob",
        "http", "keyring", "os", "pathlib", "requests", "shutil", "socket",
        "sqlite3", "subprocess", "urllib",
    })
    assert names.isdisjoint({
        "Popen", "codesign", "copy", "copy2", "decrypt", "exec", "iglob",
        "iterdir", "listdir", "open", "rglob", "scandir", "spawn", "system",
        "walk",
    })


def test_package_initializer_only_reexports_the_contract_api():
    tree = ast.parse(PACKAGE.read_text(encoding="utf-8"))
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]

    assert len(imports) == 5
    assert [(node.level, node.module) for node in imports] == [
        (1, "contracts"), (1, "keystore"), (1, "snapshot"),
        (1, "coordinator"), (1, "source_locator"),
    ]
    assert {alias.name for alias in imports[0].names} == {
        "AcquisitionOutcome", "AcquisitionReadiness", "AcquisitionState",
        "OpaqueHandle", "PreparedSource",
    }
    assert {alias.name for alias in imports[1].names} == {
        "KeyDescriptor", "KeyStore", "KeyStoreError", "SecretBytes",
    }
    assert {alias.name for alias in imports[2].names} == {"EncryptedSource"}
    assert {alias.name for alias in imports[3].names} == {
        "AcquisitionCoordinator", "AcquisitionSourceSet",
    }
    assert {alias.name for alias in imports[4].names} == {
        "LocatedSource", "SourceLocator",
    }


def test_product_selection_stays_visual_and_only_owned_adapter_imports_acquisition(
    monkeypatch,
):
    monkeypatch.syspath_prepend(str(ROOT / "bridge"))
    message_source = importlib.import_module("message_source")
    store_access = importlib.import_module("store_access")
    monkeypatch.delenv(store_access.MESSAGE_SOURCE_ENV, raising=False)

    assert store_access.selected_source_name() == message_source.SOURCE_VISUAL
    importers = sorted(
        path.relative_to(ROOT / "bridge").as_posix()
        for path in (ROOT / "bridge").rglob("*.py")
        if "__pycache__" not in path.parts
        and "tests" not in path.parts
        and "acquisition" in _imported_roots(path)
    )
    assert importers == [
        "acquired_database_source.py",
        "database_bootstrap.py",
    ]


def test_provider_stays_unwired_from_acquisition():
    for path in (ROOT / "wechatdb" / "provider").glob("*.py"):
        assert "acquisition" not in _imported_roots(path), path.name
    assert _imported_roots(CONTRACTS).isdisjoint({"bridge", "wechatdb"})
