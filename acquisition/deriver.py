"""On-demand per-database key derivation for WeChat 4.1.15+."""

from __future__ import annotations

import hashlib
from typing import Protocol

from .decryptor import KEY_SIZE, SALT_SIZE, DatabaseKeyError
from .keystore import SecretBytes

ACCOUNT_KDF_ITERATIONS: int = 256000
ACCOUNT_KDF_PRF: str = "sha512"
ACCOUNT_PASSPHRASE_PROFILE: str = "wechat-passphrase-pbkdf2-sha512-256000/v1"


class KeyDeriver(Protocol):
    @property
    def profile_id(self) -> str:
        ...

    def derive(self, account_secret: SecretBytes, salt: bytes) -> SecretBytes:
        ...


class DatabaseKeyDeriver:
    """Derives a database-specific 32-byte encryption key from an account secret.

    WeChat 4.1.15+ uses a 32-byte account passphrase. For each database, the 32-byte
    AES-256 encryption key is derived using PBKDF2-HMAC-SHA512 with 256,000 iterations
    and the database's 16-byte SQLCipher salt.
    """

    def __init__(self, iterations: int = ACCOUNT_KDF_ITERATIONS) -> None:
        self._iterations = iterations
        if iterations == ACCOUNT_KDF_ITERATIONS:
            self._profile_id = ACCOUNT_PASSPHRASE_PROFILE
        else:
            self._profile_id = f"wechat-passphrase-pbkdf2-sha512-{iterations}/custom"

    @property
    def profile_id(self) -> str:
        return self._profile_id

    def derive(self, account_secret: SecretBytes, salt: bytes) -> SecretBytes:
        if (not isinstance(account_secret, SecretBytes)
                or len(account_secret.value) != KEY_SIZE
                or not isinstance(salt, (bytes, bytearray))
                or len(salt) != SALT_SIZE):
            raise DatabaseKeyError()
        derived = hashlib.pbkdf2_hmac(
            ACCOUNT_KDF_PRF,
            account_secret.value,
            bytes(salt),
            self._iterations,
            dklen=KEY_SIZE,
        )
        return SecretBytes(derived)


class PassthroughDeriver:
    """Explicit test-only deriver for synthetic tests that bypass KDF derivation."""

    def derive(self, account_secret: SecretBytes, salt: bytes) -> SecretBytes:
        return account_secret
