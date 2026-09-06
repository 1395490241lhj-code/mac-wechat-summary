"""The consent gate: three conditions, fail closed, no bypass.

Nothing here touches the real preference domain. The app's state is read
through an injected reader in every test, so the suite cannot depend on whether
WeChat Companion has ever run on the machine executing it.
"""

from __future__ import annotations

import plistlib

import pytest

import memory_consent as consent
from conftest import app_state
from memory_store import MemoryStore


def environment(tmp_path, **overrides):
    values = {
        consent.MEMORY_ENABLED_ENV: "1",
        consent.MEMORY_DB_PATH_ENV: str(tmp_path / "memory.sqlite"),
    }
    values.update(overrides)
    return {key: value for key, value in values.items() if value is not None}


# --- the operator's activation gate ------------------------------------------


def test_all_three_conditions_met_allows_a_store(tmp_path):
    decision = consent.resolve_consent(environment(tmp_path), lambda: app_state(True, generation=7))
    assert decision.allowed
    assert decision.state == "consented"
    assert decision.require() == str(tmp_path / "memory.sqlite")
    assert decision.consent_generation == 7
    assert decision.evidence == (
        "operator_opt_in:present",
        "path:present",
        "app_consent:on",
    )


def test_without_the_operator_opt_in_nothing_opens(tmp_path):
    decision = consent.resolve_consent(
        environment(tmp_path, **{consent.MEMORY_ENABLED_ENV: None}), lambda: app_state(True)
    )
    assert not decision.allowed
    assert decision.state == "memory_disabled"
    assert decision.database_path is None


def test_the_opt_in_must_be_exactly_one(tmp_path):
    for value in ("0", "true", "YES", "yes", " 1", ""):
        decision = consent.resolve_consent(
            environment(tmp_path, **{consent.MEMORY_ENABLED_ENV: value}), lambda: app_state(True)
        )
        assert decision.state == "memory_disabled"


def test_there_is_no_default_database_path(tmp_path):
    decision = consent.resolve_consent(
        environment(tmp_path, **{consent.MEMORY_DB_PATH_ENV: "   "}), lambda: app_state(True)
    )
    assert not decision.allowed
    assert decision.state == "memory_not_configured"


def test_the_environment_alone_cannot_authorise(tmp_path):
    """Activation is not consent: both variables set, app silent -> denied."""
    decision = consent.resolve_consent(environment(tmp_path), lambda: None)
    assert not decision.allowed
    assert decision.state == "consent_state_missing"


# --- the app's consent state ---------------------------------------------------


def test_a_fresh_install_is_denied(tmp_path):
    """No state written yet: absence is "no", not "unknown"."""
    decision = consent.resolve_consent(environment(tmp_path), lambda: None)
    assert decision.state == "consent_state_missing"
    assert "app_consent:missing" in decision.evidence


def test_consent_off_in_the_app_refuses(tmp_path):
    decision = consent.resolve_consent(environment(tmp_path), lambda: app_state(False, generation=3))
    assert not decision.allowed
    assert decision.state == "consent_withheld"
    assert decision.consent_generation == 3


def test_revocation_takes_effect_on_the_next_decision(tmp_path):
    states = iter([app_state(True, generation=1), app_state(False, generation=2)])
    reader = lambda: next(states)  # noqa: E731
    assert consent.resolve_consent(environment(tmp_path), reader).allowed
    assert consent.resolve_consent(environment(tmp_path), reader).state == "consent_withheld"


def test_memory_storage_is_its_own_field_in_the_state(tmp_path):
    """The app writes both from one decision today; the gate reads the memory one."""
    decision = consent.resolve_consent(
        environment(tmp_path),
        lambda: app_state(True, allowsMemoryStorage=False),
    )
    assert decision.state == "consent_withheld"


@pytest.mark.parametrize(
    "broken",
    [
        app_state(True, version=2),
        app_state(True, version="1"),
        {key: value for key, value in app_state(True).items() if key != "allowsMemoryStorage"},
        app_state(True, allowsLocalMessageStorage="yes"),
        app_state(True, allowsLocalMessageStorage=1),
        app_state(True, generation=-1),
        app_state(True, generation=True),
        app_state(True, updatedAt="today"),
        {},
    ],
    ids=[
        "unknown-version", "string-version", "missing-field", "string-bool",
        "int-bool", "negative-generation", "bool-generation", "string-time", "empty",
    ],
)
def test_a_malformed_state_fails_closed_wholesale(tmp_path, broken):
    decision = consent.resolve_consent(environment(tmp_path), lambda: broken)
    assert not decision.allowed
    assert decision.state == "consent_state_malformed"


def test_a_reader_that_raises_is_unobservable_not_granted(tmp_path):
    def explode():
        raise OSError("no preference domain")

    decision = consent.resolve_consent(environment(tmp_path), explode)
    assert decision.state == "consent_unobservable"


def test_a_refusal_cannot_be_turned_into_an_open(tmp_path):
    decision = consent.resolve_consent(environment(tmp_path), lambda: app_state(False))
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


def test_an_existing_memory_database_does_not_imply_consent(tmp_path):
    """Consent is never inferred from a file being there."""
    with MemoryStore.open(
        consent.resolve_consent(environment(tmp_path), lambda: app_state(True))
    ):
        pass
    assert (tmp_path / "memory.sqlite").exists()
    decision = consent.resolve_consent(environment(tmp_path), lambda: None)
    assert decision.state == "consent_state_missing"


# --- the macOS reader --------------------------------------------------------


def no_defaults_tool(*args, **kwargs):
    raise OSError("no defaults tool")


def test_the_plist_fallback_reads_the_app_state(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Library" / "Preferences").mkdir(parents=True)
    target = home / "Library" / "Preferences" / f"{consent.APP_PREFERENCE_DOMAIN}.plist"
    target.write_bytes(plistlib.dumps({consent.CONSENT_STATE_KEY: app_state(True)}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(consent.subprocess, "run", no_defaults_tool)
    raw = consent.read_app_consent_state_macos()
    assert consent.ConsentState.parse(raw).allows_memory_storage is True


def test_a_missing_preference_file_is_no_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "empty"))
    monkeypatch.setattr(consent.subprocess, "run", no_defaults_tool)
    assert consent.read_app_consent_state_macos() is None


def test_a_domain_without_the_key_is_no_state(tmp_path, monkeypatch):
    """The plain flag alone is not the state: an older app is a fresh install here."""
    home = tmp_path / "home"
    (home / "Library" / "Preferences").mkdir(parents=True)
    target = home / "Library" / "Preferences" / f"{consent.APP_PREFERENCE_DOMAIN}.plist"
    target.write_bytes(plistlib.dumps({"persistence.allowsLocalMessageStorage": True}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(consent.subprocess, "run", no_defaults_tool)
    assert consent.read_app_consent_state_macos() is None


def test_the_defaults_export_path_is_parsed_as_a_plist(monkeypatch):
    class Completed:
        returncode = 0
        stdout = plistlib.dumps({consent.CONSENT_STATE_KEY: app_state(False, generation=4)})

    monkeypatch.setattr(consent.subprocess, "run", lambda *a, **k: Completed())
    raw = consent.read_app_consent_state_macos()
    assert consent.ConsentState.parse(raw).generation == 4
