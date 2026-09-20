"""Authenticated decryption for the current proven database format."""

from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path
import secrets
import struct

from Crypto.Cipher import AES

from .keystore import SecretBytes


PAGE_SIZE = 4096
KEY_SIZE = 32
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SIZE = 64
RESERVE_SIZE = 80
SQLITE_HEADER = b"SQLite format 3\x00"


class _FixedDecryptError(Exception):
    expected = ""

    def __init__(self, reason: str | None = None) -> None:
        if reason not in {None, self.expected}:
            raise ValueError("database decrypt reason invalid")
        super().__init__(self.expected)


class DatabaseKeyError(_FixedDecryptError):
    expected = "database key invalid"


class DatabaseFormatError(_FixedDecryptError):
    expected = "database format unsupported"


class DatabaseDecryptError(_FixedDecryptError):
    expected = "database decrypt failed"


def _mac_key(key: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha512", key, bytes(value ^ 0x3A for value in salt), 2, dklen=KEY_SIZE
    )


def _authenticated(page: bytes, page_number: int, mac_key: bytes) -> bool:
    start = SALT_SIZE if page_number == 1 else 0
    end = PAGE_SIZE - HMAC_SIZE
    digest = hmac.new(mac_key, page[start:end], hashlib.sha512)
    digest.update(struct.pack("<I", page_number))
    return hmac.compare_digest(digest.digest(), page[end:])


def _decrypt_page(page: bytes, page_number: int, key: bytes) -> bytes:
    iv_start = PAGE_SIZE - RESERVE_SIZE
    iv = page[iv_start:iv_start + IV_SIZE]
    start = SALT_SIZE if page_number == 1 else 0
    decrypted = AES.new(key, AES.MODE_CBC, iv).decrypt(page[start:iv_start])
    prefix = SQLITE_HEADER if page_number == 1 else b""
    return prefix + decrypted + bytes(RESERVE_SIZE)


class DatabaseDecryptor:
    def decrypt(self, source: Path, output: Path, secret: SecretBytes) -> None:
        if (not isinstance(source, Path) or not isinstance(output, Path)
                or not isinstance(secret, SecretBytes) or len(secret.value) != KEY_SIZE):
            raise DatabaseKeyError()
        temporary = output.with_name(f".{secrets.token_hex(16)}")
        try:
            size = source.stat().st_size
            if size < PAGE_SIZE or size % PAGE_SIZE:
                raise DatabaseFormatError()
            with source.open("rb") as encrypted:
                first = encrypted.read(PAGE_SIZE)
                salt = first[:SALT_SIZE]
                mac_key = _mac_key(secret.value, salt)
                if not _authenticated(first, 1, mac_key):
                    raise DatabaseKeyError()
                output.parent.mkdir(parents=True, exist_ok=True)
                with temporary.open("xb") as plaintext:
                    os.chmod(temporary, 0o600)
                    page = first
                    page_number = 1
                    while page:
                        if len(page) != PAGE_SIZE:
                            raise DatabaseFormatError()
                        if (page_number > 1
                                and not _authenticated(page, page_number, mac_key)):
                            raise DatabaseDecryptError()
                        plaintext.write(_decrypt_page(page, page_number, secret.value))
                        page = encrypted.read(PAGE_SIZE)
                        page_number += 1
                temporary.replace(output)
                os.chmod(output, 0o600)
        except (DatabaseKeyError, DatabaseFormatError, DatabaseDecryptError):
            raise
        except OSError:
            output.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
