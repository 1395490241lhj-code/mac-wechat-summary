"""The consent gate: three conditions, fail closed, no bypass.

Nothing here touches the real preference domain. The app's flag is read through
an injected reader in every test, so the suite cannot depend on whether WeChat
Companion has ever run on the machine executing it.
"""

from __future__ import annotations

import plistlib

import pytest

import memory_consent as consent
from memory_store import MemoryStore


def environment(tmp_path, **overrides):
    values = {
        consent.MEMORY_ENABLED_ENV: "1",
        consent.MEMORY_DB_PATH_ENV: str(tmp_path / "memory.sqlite"),
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


def test_all_three_conditions_met_allows_a_store(tmp_path):
    decision = consent.resolve_consent(environment(tmp_path), lambda: True)
    assert decision.allowed
    assert decision.state == "consented"
    assert decision.require() == str(tmp_path / "memory.sqlite")
    assert decision.evidence == (
        "operator_opt_in:present",
        "path:present",
        "app_consent:on",
    )


def test_without_the_operator_opt_in_nothing_opens(tmp_path):
    decision = consent.resolve_consent(
        environment(tmp_path, **{consent.MEMORY_ENABLED_ENV: None}), lambda: True
    )
    assert not decision.allowed
    assert decision.state == "memory_disabled"
    assert decision.database_path is None


def test_the_opt_in_must_be_exactly_one(tmp_path):
    for value in ("0", "true", "YES", "yes", " 1", ""):
        decision = consent.resolve_consent(
            environment(tmp_path, **{consent.MEMORY_ENABLED_ENV: value}), lambda: True
        )
        assert decision.state == "memory_disabled"


def test_there_is_no_default_database_path(tmp_path):
    decision = consent.resolve_consent(
        environment(tmp_path, **{consent.MEMORY_DB_PATH_ENV: "   "}), lambda: True
    )
    assert not decision.allowed
    assert decision.state == "memory_not_configured"


def test_consent_off_in_the_app_refuses(tmp_path):
    decision = consent.resolve_consent(environment(tmp_path), lambda: False)
    assert not decision.allowed
    assert decision.state == "consent_withheld"


def test_unobservable_consent_fails_closed_rather_than_defaulting(tmp_path):
    """The documented gap: unknown is refused, never treated as granted."""
    decision = consent.resolve_consent(environment(tmp_path), lambda: None)
    assert not decision.allowed
    assert decision.state == "consent_unobservable"
    assert "app_consent:unobservable" in decision.evidence


def test_a_reader_that_raises_is_unobservable_not_granted(tmp_path):
    def explode():
        raise OSError("no preference domain")

    decision = consent.resolve_consent(environment(tmp_path), explode)
    assert decision.state == "consent_unobservable"


def test_a_refusal_cannot_be_turned_into_an_open(tmp_path):
    decision = consent.resolve_consent(environment(tmp_path), lambda: False)
    with pytest.raises(consent.MemoryConsentError) as raised:
        decision.require()
    assert raised.value.state == "consent_withheld"
    with pytest.raises(consent.MemoryConsentError):
        MemoryStore.open(decision)
    assert not (tmp_path / "memory.sqlite").exists()


def test_a_refusal_carries_no_path_and_no_content(tmp_path):
    decision = consent.resolve_consent(environment(tmp_path), lambda: None)
    assert str(tmp_path) not in decision.detail
    assert decision.database_path is None


def test_the_plist_fallback_reads_the_app_flag(tmp_path, monkeypatch):
    """When ``defaults`` is unavailable the preference file is read directly."""
    home = tmp_path / "home"
    (home / "Library" / "Preferences").mkdir(parents=True)
    target = (
        home
        / "Library"
        / "Preferences"
        / f"{consent.APP_PREFERENCE_DOMAIN}.plist"
    )
    target.write_bytes(
        plistlib.dumps({consent.LOCAL_PERSISTENCE_CONSENT_KEY: True})
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(
        consent.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no defaults tool")),
    )
    assert consent.read_app_consent_flag_macos() is True


def test_a_missing_preference_file_is_unobservable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "empty"))
    monkeypatch.setattr(
        consent.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no defaults tool")),
    )
    assert consent.read_app_consent_flag_macos() is None
