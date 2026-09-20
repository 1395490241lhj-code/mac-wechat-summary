"""Stable descriptor identity for one selected WeChat source instance.

A KeyDescriptor answers four separate questions and must not collapse them:

* `source_fingerprint` -- which encrypted source/account instance;
* `role_token`          -- which logical database responsibility;
* `compatibility_token` -- which supported key/decryption contract;
* `record_format_version` -- which descriptor addressing contract.

Whether the *decrypted WeChat schema generation* is supported is a different
question, owned by the provider's own compatibility gate. Keeping it out means
an in-place schema change is detected without needlessly changing key identity.

Everything here is derived from evidence that is stable across ordinary
database growth. File size, page count, mtime, WAL frame count, row counts and
message counts are deliberately absent: including any of them would make normal
WeChat activity invalidate the KeyStore identity and force re-provisioning.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .decryptor import SALT_SIZE, SQLITE_HEADER, sqlcipher_profile_id
from .keystore import KeyDescriptor
from .source_locator import (
    ROLE_CONVERSATION_IDENTITY,
    ROLE_DISPLAY_IDENTITY,
    ROLE_MESSAGES,
)

#: Domain separators, versioned. A future change to what a digest means gets a
#: new version here rather than silently reinterpreting stored addresses.
SOURCE_FINGERPRINT_DOMAIN: str = "mac-wechat-summary/source-fingerprint/v1"
COMPATIBILITY_DOMAIN: str = "mac-wechat-summary/key-compatibility/v1"

#: The secret semantics this token was derived under: one account-level secret
#: addresses every required role. A future per-role model is a different
#: contract and must not reuse this token.
ACCOUNT_SHARED_SECRET: str = "account-shared-secret/v1"

#: The descriptor addressing/serialization contract, versioned independently of
#: the WeChat schema generation.
RECORD_FORMAT_VERSION: int = 1

#: The two required stable identity anchors, and the acquisition role each
#: stands for.
SESSION_ANCHOR: str = "session"
CONTACT_ANCHOR: str = "contact"

#: The logical acquisition roles every descriptor set must cover. The names come
#: from the acquisition contract, so a descriptor and a source set can never
#: disagree about what a role is called.
REQUIRED_ROLE_TOKENS: tuple[str, ...] = (
    ROLE_MESSAGES,
    ROLE_CONVERSATION_IDENTITY,
    ROLE_DISPLAY_IDENTITY,
)

ANCHOR_ROLES: dict[str, str] = {
    SESSION_ANCHOR: ROLE_CONVERSATION_IDENTITY,
    CONTACT_ANCHOR: ROLE_DISPLAY_IDENTITY,
}


class DescriptorEvidenceError(Exception):
    """The source does not carry the stable evidence a descriptor needs.

    Fixed, content-free copy: a caller is told the evidence is unavailable, and
    never which path, account or file failed.
    """

    def __init__(self) -> None:
        super().__init__("source descriptor evidence unavailable")


def _canonical(*parts: bytes) -> bytes:
    """Length-prefixed canonical encoding of the fields being digested.

    Boundaries are stated by the encoding rather than inferred from the data.
    Joining with a separator would not do: a 16-byte salt may itself contain
    the separator byte, which would let two different field lists produce the
    same bytes.
    """
    encoded = bytearray()
    for part in parts:
        encoded += len(part).to_bytes(4, "big")
        encoded += part
    return bytes(encoded)


def sqlcipher_salt(main: Path) -> bytes:
    """The 16-byte SQLCipher salt: the first bytes of the encrypted main file.

    Structural evidence only. A file that is missing, unreadable, too short, or
    that carries a plaintext SQLite header is not an encrypted source this
    contract can name, and is refused rather than guessed at.
    """
    try:
        with Path(main).open("rb") as stream:
            header = stream.read(SALT_SIZE)
    except OSError:
        raise DescriptorEvidenceError() from None
    if len(header) != SALT_SIZE or header.startswith(SQLITE_HEADER):
        raise DescriptorEvidenceError()
    return header


def source_fingerprint(session_main: Path, contact_main: Path) -> str:
    """The stable identity of one encrypted source/account instance.

    Derived only from the two required identity anchors' SQLCipher salts, so
    ordinary growth -- more messages, another message part, a larger file, a
    new WAL -- leaves it unchanged. Message-part salts are deliberately not
    included: additional shards may legitimately appear as the account grows,
    and that must not force key re-provisioning.

    Lowercase 64-hex. If either anchor is missing, unreadable, too short, or
    plaintext, this fails closed instead of naming a source it cannot prove.
    """
    digest = hashlib.sha256()
    digest.update(_canonical(
        SOURCE_FINGERPRINT_DOMAIN.encode("utf-8"),
        SESSION_ANCHOR.encode("utf-8"),
        sqlcipher_salt(session_main),
        CONTACT_ANCHOR.encode("utf-8"),
        sqlcipher_salt(contact_main),
    ))
    return digest.hexdigest()


def compatibility_token(role_token: str) -> str:
    """Which key/decryption contract a stored key was proven against.

    Stable across source growth, and derivable without decrypted data. It names
    the decryption profile by its one canonical identifier rather than restating
    the profile's parameters, and it records the account-shared-secret
    semantics, so a change to either invalidates addressing deliberately.
    """
    digest = hashlib.sha256()
    digest.update(_canonical(
        COMPATIBILITY_DOMAIN.encode("utf-8"),
        role_token.encode("utf-8"),
        sqlcipher_profile_id().encode("utf-8"),
        ACCOUNT_SHARED_SECRET.encode("utf-8"),
    ))
    return digest.hexdigest()


def descriptor_for(fingerprint: str, role_token: str) -> KeyDescriptor:
    """The descriptor addressing one role of one source instance."""
    return KeyDescriptor(
        fingerprint, role_token, RECORD_FORMAT_VERSION,
        compatibility_token(role_token),
    )


def descriptors_for(fingerprint: str) -> dict[str, KeyDescriptor]:
    """Every required role's descriptor, sharing one source fingerprint.

    Message parts share the message role's descriptor; they are not given
    identities based on how many shards happen to exist today.
    """
    return {
        role_token: descriptor_for(fingerprint, role_token)
        for role_token in REQUIRED_ROLE_TOKENS
    }

