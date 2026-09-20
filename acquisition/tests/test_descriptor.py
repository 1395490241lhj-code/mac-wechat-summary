"""Descriptor identity is stable across growth and free of personal evidence."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "bridge"):
    sys.path.insert(0, str(path))

import acquisition.descriptor as descriptor  # noqa: E402
from acquisition import KeyDescriptor  # noqa: E402
from acquisition.decryptor import sqlcipher_profile_id  # noqa: E402
from acquisition.descriptor import (  # noqa: E402
    DescriptorEvidenceError,
    REQUIRED_ROLE_TOKENS,
    descriptor_for,
    descriptors_for,
    source_fingerprint,
    sqlcipher_salt,
)

SESSION_SALT = bytes(range(16))
CONTACT_SALT = bytes(range(16, 32))
MESSAGE_SALT = bytes(range(32, 48))


def _encrypted(path: Path, salt: bytes, pages: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(salt + bytes(4080) * pages)


def _anchors(root: Path, *, session_salt=SESSION_SALT, contact_salt=CONTACT_SALT):
    session = root / "session" / "session.db"
    contact = root / "contact" / "contact.db"
    _encrypted(session, session_salt)
    _encrypted(contact, contact_salt)
    return session, contact


def test_the_same_anchor_evidence_gives_the_same_fingerprint(tmp_path):
    session, contact = _anchors(tmp_path)

    assert source_fingerprint(session, contact) == source_fingerprint(session, contact)
    assert len(source_fingerprint(session, contact)) == 64
    assert source_fingerprint(session, contact) == source_fingerprint(session, contact).lower()


def test_ordinary_growth_of_the_anchors_does_not_change_the_fingerprint(tmp_path):
    session, contact = _anchors(tmp_path)
    before = source_fingerprint(session, contact)

    # More pages, a new WAL, a different mtime: all ordinary WeChat activity.
    _encrypted(session, SESSION_SALT, pages=4)
    (session.parent / "session.db-wal").write_bytes(b"wal" * 100)
    os.utime(contact, (1_600_000_000, 1_600_000_000))

    assert source_fingerprint(session, contact) == before


def test_an_additional_message_part_does_not_change_the_fingerprint(tmp_path):
    session, contact = _anchors(tmp_path)
    before = source_fingerprint(session, contact)

    _encrypted(tmp_path / "message" / "message_0.db", MESSAGE_SALT)
    after_first = source_fingerprint(session, contact)
    _encrypted(tmp_path / "message" / "message_1.db", bytes(reversed(MESSAGE_SALT)))

    assert after_first == before
    assert source_fingerprint(session, contact) == before


@pytest.mark.parametrize("which", ["session", "contact"])
def test_changing_either_anchor_salt_changes_the_fingerprint(tmp_path, which):
    session, contact = _anchors(tmp_path)
    before = source_fingerprint(session, contact)

    other = bytes(reversed(SESSION_SALT))
    if which == "session":
        _encrypted(session, other)
    else:
        _encrypted(contact, other)

    assert source_fingerprint(session, contact) != before


def test_relocating_the_anchors_does_not_change_the_fingerprint(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    a_session, a_contact = _anchors(first)
    b_session, b_contact = _anchors(second)

    assert source_fingerprint(a_session, a_contact) == source_fingerprint(b_session, b_contact)


@pytest.mark.parametrize("bad", [b"", b"short", b"SQLite format 3\x00" + bytes(200)])
def test_anchors_without_encrypted_salt_evidence_fail_closed(tmp_path, bad):
    session, contact = _anchors(tmp_path)
    session.write_bytes(bad)

    with pytest.raises(DescriptorEvidenceError) as raised:
        source_fingerprint(session, contact)
    assert str(tmp_path) not in str(raised.value)
    assert "session" not in str(raised.value)


def test_a_missing_anchor_fails_closed(tmp_path):
    session, contact = _anchors(tmp_path)
    contact.unlink()

    with pytest.raises(DescriptorEvidenceError):
        source_fingerprint(session, contact)


def test_role_tokens_produce_role_distinct_descriptors(tmp_path):
    session, contact = _anchors(tmp_path)
    fingerprint = source_fingerprint(session, contact)

    descriptors = descriptors_for(fingerprint)

    assert set(descriptors) == set(REQUIRED_ROLE_TOKENS)
    assert all(value.source_fingerprint == fingerprint for value in descriptors.values())
    tokens = {value.compatibility_token for value in descriptors.values()}
    assert len(tokens) == len(REQUIRED_ROLE_TOKENS)
    assert all(value.record_format_version == 1 for value in descriptors.values())


def test_the_compatibility_token_is_stable_for_one_profile_and_role(tmp_path):
    session, contact = _anchors(tmp_path)
    fingerprint = source_fingerprint(session, contact)

    assert descriptor_for(fingerprint, REQUIRED_ROLE_TOKENS[0]) == descriptor_for(
        fingerprint, REQUIRED_ROLE_TOKENS[0])


def test_changing_the_crypto_profile_changes_the_compatibility_token(tmp_path, monkeypatch):
    session, contact = _anchors(tmp_path)
    fingerprint = source_fingerprint(session, contact)
    before = descriptor_for(fingerprint, REQUIRED_ROLE_TOKENS[0])

    monkeypatch.setattr(descriptor, "sqlcipher_profile_id", lambda: "sqlcipher-9/other")

    assert descriptor_for(fingerprint, REQUIRED_ROLE_TOKENS[0]) != before


def test_the_profile_identifier_is_derived_not_restated():
    identifier = sqlcipher_profile_id()

    assert "aes-256-cbc" in identifier and "page-4096" in identifier
    assert "pbkdf2-sha512-2" in identifier


def test_neither_digest_carries_personal_or_path_evidence(tmp_path):
    session, contact = _anchors(tmp_path)
    fingerprint = source_fingerprint(session, contact)
    token = descriptor_for(fingerprint, REQUIRED_ROLE_TOKENS[0]).compatibility_token

    for value in (fingerprint, token):
        assert value.isalnum() and len(value) == 64
        for leaked in ("session", "contact", "Users", "tmp", "wxid"):
            assert leaked not in value


def test_the_salt_is_read_from_the_encrypted_main_file(tmp_path):
    session, _ = _anchors(tmp_path)

    assert sqlcipher_salt(session) == SESSION_SALT

