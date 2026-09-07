"""The explicit foreground sync: consent-gated, idempotent, never scheduled."""

from __future__ import annotations

import pytest

import memory_consent as consent
import memory_sync
from conftest import app_state, conversation, visual_message
from memory_query import MemoryQueryService
from memory_store import MemoryStore
from message_source import SOURCE_VISUAL, MessageSourceError, SourceStatus


class FakeSource:
    name = SOURCE_VISUAL

    def __init__(self, messages, *, fail=None):
        self._messages = messages
        self._fail = fail
        self.calls = 0

    def status(self):
        return SourceStatus(source=self.name, ready=True, state="ready")

    def list_conversations(self, limit):
        self.calls += 1
        if self._fail:
            raise MessageSourceError(self._fail, "cannot")
        return [conversation(7, "项目组")]

    def get_messages(self, conversation_id, limit, before_sequence=None):
        return self._messages[:limit]

    def get_recent_messages(self, since, limit):
        raise MessageSourceError("unsupported", "unused")


def env(tmp_path):
    return {consent.MEMORY_ENABLED_ENV: "1", consent.MEMORY_DB_PATH_ENV: str(tmp_path / "memory.sqlite")}


MESSAGES = [visual_message(1, 7, "明天开会"), visual_message(2, 7, "好的")]


def test_a_consented_sync_ingests_and_reports_counts_only(tmp_path):
    report = memory_sync.sync_from_source(FakeSource(MESSAGES), environment=env(tmp_path),
                                          read_app_consent_state=lambda: app_state(True, generation=3))
    assert report.ok
    summary = report.summary()
    assert (summary["messages_inserted"], summary["messages_updated"]) == (2, 0)
    assert summary["consent_generation"] == 3
    assert not any(isinstance(v, str) and "开会" in v for v in summary.values())
    with MemoryStore.open(consent.resolve_consent(env(tmp_path), lambda: app_state(True))) as store:
        assert len(MemoryQueryService(store).search(text="开会").items) == 1


def test_a_second_sync_is_idempotent(tmp_path):
    for _ in range(2):
        report = memory_sync.sync_from_source(FakeSource(MESSAGES), environment=env(tmp_path),
                                              read_app_consent_state=lambda: app_state(True))
    assert (report.summary()["messages_inserted"], report.summary()["messages_updated"]) == (0, 2)


@pytest.mark.parametrize("state,expected", [
    (None, "consent_state_missing"),
    ({"version": 9}, "consent_state_malformed"),
    (app_state(False), "consent_withheld"),
])
def test_a_refused_consent_syncs_nothing_and_touches_no_source(tmp_path, state, expected):
    source = FakeSource(MESSAGES)
    report = memory_sync.sync_from_source(source, environment=env(tmp_path), read_app_consent_state=lambda: state)
    assert not report.ok
    assert report.summary()["state"] == expected
    assert source.calls == 0
    assert not (tmp_path / "memory.sqlite").exists()


def test_activation_variables_alone_do_not_sync(tmp_path):
    report = memory_sync.sync_from_source(FakeSource(MESSAGES), environment={}, read_app_consent_state=lambda: app_state(True))
    assert report.summary()["state"] == "memory_disabled"


def test_a_refusing_source_records_a_failed_run_and_writes_nothing(tmp_path):
    report = memory_sync.sync_from_source(FakeSource([], fail="reader_unavailable"), environment=env(tmp_path),
                                          read_app_consent_state=lambda: app_state(True))
    assert not report.ok
    assert report.summary()["failure_state"] == "reader_unavailable"


def test_the_cli_is_foreground_and_prints_counts(tmp_path, monkeypatch, capsys):
    for key, value in env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(memory_sync, "read_app_consent_state_macos", lambda: app_state(True))
    monkeypatch.setattr(memory_sync, "resolve_consent",
                        lambda environment, reader: consent.resolve_consent(environment, lambda: app_state(True)))
    code = memory_sync.main(["--message-limit", "10"], build_source=lambda: FakeSource(MESSAGES))
    out = capsys.readouterr().out
    assert code == 0
    assert "messages_inserted: 2" in out
    assert "开会" not in out and str(tmp_path) not in out


def test_the_cli_exits_nonzero_on_refusal(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(consent.MEMORY_ENABLED_ENV, raising=False)
    code = memory_sync.main([], build_source=lambda: FakeSource(MESSAGES))
    assert code == 1
    assert "memory_disabled" in capsys.readouterr().out


def test_nothing_schedules_or_polls():
    """Structural: no timing, threading or event-loop module is imported."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(memory_sync))
    imported = {
        (alias.name if isinstance(node, ast.Import) else node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    }
    assert imported.isdisjoint({"sched", "threading", "asyncio", "signal", "subprocess"})
    assert "sleep" not in inspect.getsource(memory_sync)
    assert "while True" not in inspect.getsource(memory_sync.main)


# --- M2.2c: the selected source, never another -------------------------------------


def test_the_cli_refuses_when_the_selected_source_cannot_be_built(tmp_path, monkeypatch, capsys):
    """A database selection without its reader stops the sync; nothing else runs."""
    for key, value in env(tmp_path).items():
        monkeypatch.setenv(key, value)
    def selected():
        raise MessageSourceError("reader_not_configured", "No external reader configured.")
    code = memory_sync.main([], build_source=selected)
    out = capsys.readouterr().out
    assert code == 1 and "state: reader_not_configured" in out
    assert not (tmp_path / "memory.sqlite").exists()


@pytest.mark.parametrize("selection,expected", [
    ("database", "reader_not_configured"),
    ("carrier-pigeon", "source_unknown"),
])
def test_build_selected_source_honours_the_bridge_rule(monkeypatch, selection, expected):
    monkeypatch.setenv("WECHAT_COMPANION_MESSAGE_SOURCE", selection)
    monkeypatch.setenv("WECHAT_COMPANION_ALLOW_AGENT_READ", "1")
    monkeypatch.delenv("WECHAT_COMPANION_READER_BIN", raising=False)
    with pytest.raises(MessageSourceError) as raised:
        memory_sync.build_selected_source()
    assert raised.value.state == expected


def test_build_selected_source_defaults_to_visual_and_never_switches(monkeypatch):
    monkeypatch.delenv("WECHAT_COMPANION_MESSAGE_SOURCE", raising=False)
    assert memory_sync.build_selected_source().name == SOURCE_VISUAL
    monkeypatch.setenv("WECHAT_COMPANION_MESSAGE_SOURCE", "visual")
    assert memory_sync.build_selected_source().name == SOURCE_VISUAL


def test_a_successful_sync_reports_freshness(tmp_path):
    report = memory_sync.sync_from_source(FakeSource(MESSAGES), environment=env(tmp_path),
                                          read_app_consent_state=lambda: app_state(True))
    assert report.freshness["participating_sources"] == [SOURCE_VISUAL]
    assert report.freshness["sources"][SOURCE_VISUAL]["last_attempt_state"] == "succeeded"
    assert report.summary()["freshness"] is report.freshness
