"""The production wiring: recorded decision -> composition root -> Fast Lane.

These tests drive the real production entry point. Nothing here calls a
registration helper, because production no longer needs one: the recorded
source decision is the only thing that makes database mode reachable, and it is
read through the composition root exactly as a running bridge reads it.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
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
    AcquisitionOutcome,
    AcquisitionReadiness,
    AcquisitionSourceSet,
    AcquisitionState,
    EncryptedSource,
    KeyDescriptor,
    OpaqueHandle,
    PreparedSource,
    SecretBytes,
)
from acquisition.decryptor import DatabaseKeyError  # noqa: E402
from message_source import (  # noqa: E402
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    REASON_CALLER_LIMIT,
    REASON_FULL_WINDOW_OBSERVED,
    SOURCE_DATABASE,
    SOURCE_VISUAL,
    MessageSourceError,
    NormalizedConversation,
    NormalizedMessage,
    ReadCoverage,
    ReadFreshness,
    ReadResult,
    SourceStatus,
)
import acquired_database_source  # noqa: E402
import store_access  # noqa: E402
from acquired_database_source import (  # noqa: E402
    AcquiredDatabaseSource,
    acquisition_workspace_root,
    open_database_source,
    recorded_manifest_path,
)

FINGERPRINT = "a" * 64
COMPATIBILITY = "b" * 64
CONVERSATION = "wxid_fixture_conversation"


def _message_database(path: Path, conversation: str = CONVERSATION) -> None:
    digest = hashlib.md5(conversation.encode("utf-8")).hexdigest()
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE Name2Id (user_name TEXT);"
        "INSERT INTO Name2Id VALUES ('wxid_fixture_sender');"
        "CREATE TABLE Msg_" + digest + " ("
        "local_id INTEGER PRIMARY KEY, server_id INTEGER, local_type INTEGER, "
        "real_sender_id INTEGER, create_time INTEGER, message_content BLOB, "
        "source BLOB, packed_info_data BLOB);"
        "INSERT INTO Msg_" + digest + " "
        "(local_id, server_id, local_type, real_sender_id, create_time, message_content) "
        "VALUES (1, 101, 1, 1, 100, X'66697874757265');"
    )
    connection.commit()
    connection.close()


def _session_database(path: Path, conversation: str = CONVERSATION) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE SessionTable (username TEXT)")
    connection.execute("INSERT INTO SessionTable VALUES (?)", (conversation,))
    connection.commit()
    connection.close()


def _descriptor(role: str) -> dict[str, object]:
    return {
        "source_fingerprint": FINGERPRINT,
        "role_token": role,
        "record_format_version": 1,
        "compatibility_token": COMPATIBILITY,
    }


def _entry(path: Path, role: str) -> dict[str, object]:
    return {"main": str(path), "wal": None, "shm": None, "key_descriptor": _descriptor(role)}


def _write_manifest(
    home: Path, message_path: Path, session_path: Path | None = None
) -> Path:
    if session_path is None:
        session_path = message_path.with_name("session.sqlite")
        if not session_path.exists():
            _session_database(session_path)
    path = recorded_manifest_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "manifest_version": 1,
        "selected_source": "database",
        "messages": [_entry(message_path, "messages")],
        "conversation_identity": _entry(session_path, "session"),
        "display_identity": None,
    }), encoding="utf-8")
    return path


class _KeyStore:
    def __init__(self, secret: bytes | None = b"synthetic-secret") -> None:
        self._secret = secret

    def load(self, descriptor: KeyDescriptor) -> SecretBytes | None:
        assert isinstance(descriptor, KeyDescriptor)
        return SecretBytes(self._secret) if self._secret is not None else None


class _Decryptor:
    def decrypt(self, source: Path, output: Path, secret: SecretBytes) -> None:
        assert isinstance(secret, SecretBytes)
        shutil.copyfile(source, output)


class _RejectingDecryptor:
    def decrypt(self, source: Path, output: Path, secret: SecretBytes) -> None:
        raise DatabaseKeyError()


class _FailingCoordinator:
    """An acquisition that never becomes READY."""

    def __init__(self, state: AcquisitionState) -> None:
        self._state = state

    @contextmanager
    def prepare(self, source_set, *, database_mode_enabled):
        assert database_mode_enabled is True
        yield AcquisitionOutcome(AcquisitionReadiness(self._state))


class _Visual:
    name = SOURCE_VISUAL

    def __init__(self) -> None:
        self.calls: list[str] = []

    @staticmethod
    def empty_coverage() -> ReadCoverage:
        return ReadCoverage(
            status=COVERAGE_COMPLETE, reason=REASON_FULL_WINDOW_OBSERVED,
            requested_start=None, requested_end=None, observed_through=None,
            complete_through=None, freshness=ReadFreshness.UNKNOWN,
            truncated=False, item_count=0)

    def _empty(self, method: str) -> ReadResult:
        self.calls.append(method)
        return ReadResult(items=(), coverage=self.empty_coverage())

    def status(self) -> SourceStatus:
        self.calls.append("status")
        return SourceStatus(source=SOURCE_VISUAL, ready=True, state="ready")

    def list_conversations(self, limit: int) -> ReadResult:
        return self._empty("list_conversations")

    def get_messages(self, conversation_id, limit, before_sequence=None) -> ReadResult:
        return self._empty("get_messages")

    def get_recent_messages(self, since_observed_at, limit) -> ReadResult:
        return self._empty("get_recent_messages")


class _PopulatedVisual(_Visual):
    """A store with a row in it, so a mis-tagged fallback is visible."""

    @staticmethod
    def populated_coverage() -> ReadCoverage:
        return ReadCoverage(
            status=COVERAGE_PARTIAL, reason=REASON_CALLER_LIMIT,
            requested_start=None, requested_end=None, observed_through=5.0,
            complete_through=None, freshness=ReadFreshness.UNKNOWN,
            truncated=True, item_count=1)

    def list_conversations(self, limit: int) -> ReadResult:
        self.calls.append("list_conversations")
        return ReadResult(items=(NormalizedConversation(
            id=1, title="store chat", first_seen_at=None, last_seen_at=5.0,
            source=SOURCE_VISUAL),), coverage=self.populated_coverage())

    def get_recent_messages(self, since_observed_at, limit) -> ReadResult:
        self.calls.append("get_recent_messages")
        return ReadResult(items=(NormalizedMessage(
            id=2, conversation_id=1, sequence=3, sender=None, ownership="unknown",
            visible_time=None, text="store text", kind="text", confidence=0.0,
            first_observed_at=5.0, source=SOURCE_VISUAL),),
            coverage=self.populated_coverage())


def _production_source(tmp_path: Path, **kwargs):
    home = tmp_path / "home"
    message = tmp_path / "prepared.sqlite"
    session = tmp_path / "session.sqlite"
    _message_database(message)
    _session_database(session)
    _write_manifest(home, message, session)
    visual = _Visual()
    source = open_database_source(
        lambda: visual, home=home, key_store=_KeyStore(),
        decryptor=_Decryptor(), **kwargs,
    )
    return source, visual, home


# --- the production path ------------------------------------------------------


def test_a_recorded_decision_reaches_the_fast_lane_without_any_registration(tmp_path):
    source, visual, _ = _production_source(tmp_path)

    assert isinstance(source, AcquiredDatabaseSource)
    assert source.name == SOURCE_DATABASE
    result = source.list_conversations(10)
    assert len(result.items) == 1
    assert result.items[0].source == SOURCE_DATABASE
    assert visual.calls == []


def test_no_recorded_decision_means_no_database_source(tmp_path):
    home = tmp_path / "home"
    assert open_database_source(_Visual, home=home) is None
    assert not recorded_manifest_path(home).exists()


def test_a_malformed_record_means_no_database_source(tmp_path):
    home = tmp_path / "home"
    path = recorded_manifest_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert open_database_source(_Visual, home=home) is None


def test_a_record_the_reader_cannot_support_means_no_database_source(tmp_path):
    home = tmp_path / "home"
    message = tmp_path / "prepared.sqlite"
    _message_database(message)
    path = _write_manifest(home, message)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["manifest_version"] = 99
    path.write_text(json.dumps(document), encoding="utf-8")

    assert open_database_source(_Visual, home=home) is None


def test_the_composition_root_is_reached_from_the_real_selection_path(tmp_path, monkeypatch):
    sentinel = _Visual()
    seen: list[object] = []

    def spy(visual_factory, **kwargs):
        seen.append(visual_factory)
        return sentinel

    monkeypatch.setattr(acquired_database_source, "open_database_source", spy)
    monkeypatch.setenv(store_access.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(store_access.MESSAGE_SOURCE_ENV, SOURCE_DATABASE)

    assert store_access.active_source() is sentinel
    assert seen == [store_access.StoreMessageSource]


def test_a_selected_database_with_nothing_recorded_is_refused(monkeypatch, tmp_path):
    """An explicit database request is never quietly answered by visual.

    The fallback this pass adds is narrower: it lives inside the acquisition
    adapter and applies only when a recorded decision exists but the sources
    behind it cannot be read.
    """
    monkeypatch.setenv(store_access.ALLOW_READ_ENV, "1")
    monkeypatch.setenv(store_access.MESSAGE_SOURCE_ENV, SOURCE_DATABASE)
    monkeypatch.setattr(
        acquired_database_source, "recorded_manifest_path", lambda home=None: tmp_path / "none.json")

    with pytest.raises(store_access.BridgeUnavailable) as raised:
        store_access.active_source()
    assert raised.value.state == "reader_not_configured"


# --- one acquisition per operation -------------------------------------------


def test_one_logical_operation_acquires_exactly_once(tmp_path):
    source, _, _ = _production_source(tmp_path)
    coordinator = source._coordinator
    entered = 0
    original = coordinator.prepare

    @contextmanager
    def counting(*args, **kwargs):
        nonlocal entered
        entered += 1
        with original(*args, **kwargs) as outcome:
            yield outcome

    coordinator.prepare = counting
    source.list_conversations(10)
    assert entered == 1
    source.get_recent_messages(0, 10)
    assert entered == 2


def test_selection_alone_acquires_nothing(tmp_path):
    source, _, _ = _production_source(tmp_path)
    coordinator = source._coordinator
    entered = 0
    original = coordinator.prepare

    @contextmanager
    def counting(*args, **kwargs):
        nonlocal entered
        entered += 1
        with original(*args, **kwargs) as outcome:
            yield outcome

    coordinator.prepare = counting
    assert open_database_source(
        _Visual, home=tmp_path / "home", key_store=_KeyStore(), decryptor=_Decryptor()
    ) is not None
    assert entered == 0


# --- every non-ready path falls back before anything escapes ------------------


def test_missing_key_material_falls_back_to_visual(tmp_path):
    home = tmp_path / "home"
    message = tmp_path / "prepared.sqlite"
    _message_database(message)
    _write_manifest(home, message)
    visual = _Visual()
    source = open_database_source(
        lambda: visual, home=home, key_store=_KeyStore(None), decryptor=_Decryptor())

    result = source.list_conversations(10)
    assert result.items == ()
    assert visual.calls == ["list_conversations"]


def test_rejected_key_material_falls_back_to_visual(tmp_path):
    home = tmp_path / "home"
    message = tmp_path / "prepared.sqlite"
    _message_database(message)
    _write_manifest(home, message)
    visual = _Visual()
    source = open_database_source(
        lambda: visual, home=home, key_store=_KeyStore(), decryptor=_RejectingDecryptor())

    result = source.get_recent_messages(0, 10)
    assert result.items == ()
    assert visual.calls == ["get_recent_messages"]


def test_a_fallback_answer_is_never_dressed_as_the_database(tmp_path):
    """A store answer keeps the store's ids, its coverage and its name (C1).

    A recorded decision whose acquisition cannot run must not mint database
    references for rows the store produced: the follow-up read would route back
    to a database that cannot serve it, and the envelope would name the wrong
    reader.
    """
    home = tmp_path / "home"
    message = tmp_path / "prepared.sqlite"
    _message_database(message)
    _write_manifest(home, message)
    visual = _PopulatedVisual()
    source = open_database_source(
        lambda: visual, home=home, key_store=_KeyStore(None), decryptor=_Decryptor())

    listed = source.list_conversations(10)
    assert [item.id for item in listed.items] == [1]
    assert listed.coverage == _PopulatedVisual.populated_coverage()
    assert source.name == SOURCE_VISUAL

    recent = source.get_recent_messages(0, 10)
    assert [item.conversation_id for item in recent.items] == [1]
    assert source.name == SOURCE_VISUAL


def test_recorded_paths_that_are_gone_fall_back_to_visual(tmp_path):
    home = tmp_path / "home"
    _write_manifest(home, tmp_path / "missing.sqlite")
    visual = _Visual()
    source = open_database_source(
        lambda: visual, home=home, key_store=_KeyStore(), decryptor=_Decryptor())

    result = source.get_recent_messages(0, 10)
    assert result.items == ()
    assert visual.calls == ["get_recent_messages"]


def test_a_part_that_cannot_be_read_falls_back_to_visual(tmp_path):
    home = tmp_path / "home"
    message = tmp_path / "prepared.sqlite"
    connection = sqlite3.connect(message)
    connection.execute("CREATE TABLE fixture(value INTEGER)")
    connection.commit()
    connection.close()
    _write_manifest(home, message)
    visual = _Visual()
    source = open_database_source(
        lambda: visual, home=home, key_store=_KeyStore(), decryptor=_Decryptor())

    assert source.list_conversations(10).items == ()
    assert visual.calls == ["list_conversations"]


# --- no exception text crosses the boundary ----------------------------------


def test_unexpected_provider_failure_is_sanitized(tmp_path, monkeypatch):
    secret = "synthetic-sensitive-provider-text"
    source, visual, _ = _production_source(tmp_path)

    def explode(*args, **kwargs):
        raise RuntimeError(secret)

    monkeypatch.setattr(
        acquired_database_source.ShardedMessageProvider, "list_conversations", explode)
    report = source.status()

    assert report.ready is False
    assert secret not in report.state and secret not in report.detail
    assert secret not in repr(report)


def test_an_unexpected_failure_before_any_result_falls_back_to_visual(tmp_path, monkeypatch):
    secret = "synthetic-sensitive-provider-text"
    source, visual, _ = _production_source(tmp_path)

    def explode(*args, **kwargs):
        raise sqlite3.OperationalError(secret)

    monkeypatch.setattr(
        acquired_database_source.ShardedMessageProvider, "list_conversations", explode)
    result = source.list_conversations(10)

    assert result.items == ()
    assert visual.calls == ["list_conversations"]


def test_a_changed_but_parseable_generation_is_refused_as_unverified(tmp_path):
    """A renamed column fails closed before any row of it is read (P4)."""
    home = tmp_path / "home"
    message = tmp_path / "prepared.sqlite"
    connection = sqlite3.connect(message)
    connection.execute(
        "CREATE TABLE Msg_" + "c" * 32 + " ("
        "local_id INTEGER PRIMARY KEY, svr_id INTEGER, local_type INTEGER, "
        "real_sender_id INTEGER, create_time INTEGER, message_content BLOB, "
        "source BLOB, packed_info_data BLOB)")
    connection.commit()
    connection.close()
    _write_manifest(home, message)
    visual = _Visual()
    source = open_database_source(
        lambda: visual, home=home, key_store=_KeyStore(), decryptor=_Decryptor())

    assert source.status().state == "version_unverified"
    assert source.list_conversations(10).items == ()
    assert visual.calls == ["list_conversations"]


# --- source affinity ----------------------------------------------------------


def test_a_database_reference_is_tagged_and_read_back_by_the_database(tmp_path):
    source, _, _ = _production_source(tmp_path)

    listed = source.list_conversations(10)
    reference = listed.items[0].id
    assert reference < 0

    messages = source.get_messages(reference, 10)
    assert [item.conversation_id for item in messages.items] == [reference]
    assert messages.items[0].text == "fixture"


def test_the_database_refuses_a_reference_it_did_not_mint(tmp_path):
    source, _, _ = _production_source(tmp_path)

    with pytest.raises(MessageSourceError) as raised:
        source.get_messages(7, 10)
    assert raised.value.state == "conversation_reference_mismatch"


def test_a_database_origin_read_never_falls_back_when_acquisition_fails():
    visual = _Visual()
    source = AcquiredDatabaseSource(
        _FailingCoordinator(AcquisitionState.SNAPSHOT_UNSTABLE), object(),
        lambda: visual)

    with pytest.raises(MessageSourceError) as raised:
        source.get_messages(-5, 10)
    assert raised.value.state == "snapshot_unstable"
    assert visual.calls == []

    # The same failure on a read that no database reference pinned may fall
    # back, which is the boundary the previous assertion is measured against.
    assert source.list_conversations(10).items == ()
    assert visual.calls == ["list_conversations"]


def test_the_visual_source_owns_non_negative_references(tmp_path, monkeypatch):
    monkeypatch.setenv(store_access.ALLOW_READ_ENV, "1")

    assert isinstance(store_access.source_for_conversation(5), store_access.StoreMessageSource)


def test_a_database_reference_is_refused_when_nothing_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv(store_access.ALLOW_READ_ENV, "1")
    monkeypatch.setattr(
        acquired_database_source, "recorded_manifest_path",
        lambda home=None: tmp_path / "none.json")

    with pytest.raises(store_access.BridgeUnavailable) as raised:
        store_access.source_for_conversation(-5)
    assert raised.value.state == "database_unavailable"


def test_a_database_reference_still_needs_the_agent_read_opt_in(monkeypatch):
    monkeypatch.delenv(store_access.ALLOW_READ_ENV, raising=False)

    with pytest.raises(store_access.BridgeUnavailable) as raised:
        store_access.source_for_conversation(-5)
    assert raised.value.state == "agent_read_disabled"


# --- coverage is stated, not implied -----------------------------------------


def test_a_successful_database_read_states_its_full_coverage(tmp_path):
    source, _, _ = _production_source(tmp_path)

    listed = source.list_conversations(10)
    assert listed.coverage.status == COVERAGE_COMPLETE
    assert listed.coverage.reason == REASON_FULL_WINDOW_OBSERVED
    assert listed.coverage.truncated is False
    assert listed.coverage.item_count == 1

    recent = source.get_recent_messages(0, 10)
    assert recent.coverage.status == COVERAGE_COMPLETE
    assert recent.coverage.reason == REASON_FULL_WINDOW_OBSERVED
    assert recent.coverage.freshness is ReadFreshness.UNKNOWN


def test_a_fallback_answer_carries_the_visual_coverage_verbatim(tmp_path):
    home = tmp_path / "home"
    _write_manifest(home, tmp_path / "missing.sqlite")
    visual = _Visual()
    source = open_database_source(
        lambda: visual, home=home, key_store=_KeyStore(), decryptor=_Decryptor())

    result = source.list_conversations(10)
    assert result.coverage == _Visual.empty_coverage()


# --- lifetime -----------------------------------------------------------------


def test_cancellation_removes_the_ephemeral_workspace(tmp_path, monkeypatch):
    source, _, home = _production_source(tmp_path)
    root = acquisition_workspace_root(home)

    def cancelled(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(
        acquired_database_source.ShardedMessageProvider, "get_recent_messages", cancelled)
    with pytest.raises(KeyboardInterrupt):
        source.get_recent_messages(0, 10)

    assert root.exists() and list(root.iterdir()) == []


def test_an_ordinary_read_leaves_no_workspace_behind(tmp_path):
    source, _, home = _production_source(tmp_path)
    root = acquisition_workspace_root(home)

    assert len(source.get_recent_messages(0, 10).items) == 1
    assert root.exists() and list(root.iterdir()) == []


# --- ownership and isolation guards ------------------------------------------


def test_the_recorded_decision_lives_beside_the_apps_other_stores():
    assert acquired_database_source.APP_SUPPORT_COMPONENTS == (
        "Library", "Application Support", "WeChatCompanion")
    memory = (ROOT / "memory" / "memory_paths.py").read_text(encoding="utf-8")
    assert 'APP_DIRECTORY_NAME: str = "WeChatCompanion"' in memory
    swift = (ROOT / "apps" / "WeChatCompanion" / "WeChatCompanion" / "Ingestion"
             / "MessageStore.swift").read_text(encoding="utf-8")
    assert "WeChatCompanion" in swift and "applicationSupportDirectory" in swift


def test_the_wiring_never_uses_the_legacy_recursive_discovery():
    for name in ("acquired_database_source.py", "store_access.py"):
        source = (ROOT / "bridge" / name).read_text(encoding="utf-8")
        for forbidden in ("auto_detect_db_dir", "core.config", "os.walk", "rglob",
                          "PRAGMA key", "frida"):
            assert forbidden not in source, (name, forbidden)


def test_generic_reader_contract_contains_no_wechat_schema_identifiers():
    source = (ROOT / "bridge" / "message_source.py").read_text(encoding="utf-8")
    for forbidden in ("Msg_", "Name2Id", "real_sender_id", "local_type"):
        assert forbidden not in source
