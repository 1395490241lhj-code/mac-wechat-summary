"""Source-neutral database key storage contract."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Protocol


_DIGEST = re.compile(r"[0-9a-f]{64}")
_ROLE = re.compile(r"[a-z][a-z0-9_-]{0,31}")


@dataclass(frozen=True, slots=True)
class SecretBytes:
    value: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.value) is not bytes or not self.value:
            raise ValueError("secret bytes invalid")


@dataclass(frozen=True, slots=True)
class KeyDescriptor:
    source_fingerprint: str
    role_token: str
    record_format_version: int
    compatibility_token: str

    def __post_init__(self) -> None:
        if (not isinstance(self.source_fingerprint, str)
                or not _DIGEST.fullmatch(self.source_fingerprint)
                or not isinstance(self.role_token, str)
                or not _ROLE.fullmatch(self.role_token)
                or type(self.record_format_version) is not int
                or not 1 <= self.record_format_version <= 9999
                or not isinstance(self.compatibility_token, str)
                or not _DIGEST.fullmatch(self.compatibility_token)):
            raise ValueError("key descriptor invalid")

    @property
    def account_id(self) -> str:
        return (f"v{self.record_format_version}:{self.source_fingerprint}:"
                f"{self.role_token}:{self.compatibility_token}")


class KeyStoreError(Exception):
    """A fixed, content-free failure from database key storage."""

    def __init__(self, reason: str) -> None:
        if reason not in {
            "key store access failure", "key store duplicate update conflict"
        }:
            raise ValueError("key store reason invalid")
        super().__init__(reason)


class KeyLookup(Protocol):
    """The read side of key storage, and the whole of what acquisition consumes.

    The coordinator never writes, lists or deletes keys; it looks one descriptor
    up. Stating that here is what lets a caller validate a *candidate* secret
    against a source without first making it durable: an in-memory lookup that
    answers from the candidate satisfies this protocol exactly as the KeyStore
    does, and nothing else about acquisition changes.
    """

    def load(self, descriptor: KeyDescriptor) -> SecretBytes | None:
        ...


class KeyStore:
    """Durable key storage; the production implementation of KeyLookup."""

    def __init__(self, backend: object | None = None) -> None:
        if backend is None:
            from .macos_keychain import MacOSKeychain

            backend = MacOSKeychain()
        self._backend = backend

    def load(self, descriptor: KeyDescriptor) -> SecretBytes | None:
        if not isinstance(descriptor, KeyDescriptor):
            raise ValueError("key descriptor invalid")
        value = self._backend.load(descriptor.account_id)
        return SecretBytes(value) if value is not None else None

    def put(self, descriptor: KeyDescriptor, secret: SecretBytes) -> None:
        if not isinstance(descriptor, KeyDescriptor):
            raise ValueError("key descriptor invalid")
        if not isinstance(secret, SecretBytes):
            raise ValueError("secret bytes invalid")
        self._backend.put(descriptor.account_id, secret.value)

    def delete(self, descriptor: KeyDescriptor) -> bool:
        if not isinstance(descriptor, KeyDescriptor):
            raise ValueError("key descriptor invalid")
        return self._backend.delete(descriptor.account_id)
