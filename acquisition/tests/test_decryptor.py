from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
import struct

from Crypto.Cipher import AES
import pytest


PAGE = 4096
RESERVE = 80
HEADER = b"SQLite format 3\x00"
KEY = bytes(range(32))
SALT = bytes(range(16))


def _mac_key(key, salt):
    return hashlib.pbkdf2_hmac(
        "sha512", key, bytes(value ^ 0x3A for value in salt), 2, dklen=32
    )


def _encrypted_page(key: bytes, page_number: int, body: bytes) -> bytes:
    iv = bytes([page_number]) * 16
    encrypted = AES.new(key, AES.MODE_CBC, iv).encrypt(body)
    prefix = SALT + encrypted if page_number == 1 else encrypted
    authenticated = prefix[16:] + iv if page_number == 1 else prefix + iv
    digest = hmac.new(_mac_key(key, SALT), authenticated, hashlib.sha512)
    digest.update(struct.pack("<I", page_number))
    return prefix + iv + digest.digest()


def _database() -> tuple[bytes, bytes]:
    first_body = bytes((index % 251 for index in range(PAGE - RESERVE - 16)))
    second_body = bytes(((index + 7) % 251 for index in range(PAGE - RESERVE)))
    encrypted = _encrypted_page(KEY, 1, first_body) + _encrypted_page(KEY, 2, second_body)
    plaintext = HEADER + first_body + bytes(RESERVE) + second_body + bytes(RESERVE)
    return encrypted, plaintext


def test_current_format_decrypts_byte_exact_with_secret_bytes(tmp_path):
    from acquisition import SecretBytes
    from acquisition.decryptor import DatabaseDecryptor

    encrypted, plaintext = _database()
    source = tmp_path / "encrypted"
    output = tmp_path / "plaintext"
    source.write_bytes(encrypted)
    DatabaseDecryptor().decrypt(source, output, SecretBytes(KEY))
    assert output.read_bytes() == plaintext
    assert output.stat().st_mode & 0o777 == 0o600


def test_wrong_key_is_refused_before_plaintext_is_published(tmp_path):
    from acquisition import SecretBytes
    from acquisition.decryptor import DatabaseKeyError, DatabaseDecryptor

    encrypted, _ = _database()
    source = tmp_path / "encrypted"
    output = tmp_path / "plaintext"
    source.write_bytes(encrypted)
    with pytest.raises(DatabaseKeyError, match="^database key invalid$"):
        DatabaseDecryptor().decrypt(source, output, SecretBytes(b"x" * 32))
    assert not output.exists()


def test_secret_must_be_exactly_32_bytes_at_decrypt_boundary(tmp_path):
    from acquisition import SecretBytes
    from acquisition.decryptor import DatabaseKeyError, DatabaseDecryptor

    source = tmp_path / "encrypted"
    source.write_bytes(bytes(PAGE))
    with pytest.raises(DatabaseKeyError, match="^database key invalid$"):
        DatabaseDecryptor().decrypt(source, tmp_path / "out", SecretBytes(b"short"))


def test_authenticated_page_corruption_after_key_validation_is_refused(tmp_path):
    from acquisition import SecretBytes
    from acquisition.decryptor import DatabaseDecryptError, DatabaseDecryptor

    encrypted, _ = _database()
    damaged = bytearray(encrypted)
    damaged[-1] ^= 1
    source = tmp_path / "encrypted"
    source.write_bytes(damaged)
    with pytest.raises(DatabaseDecryptError, match="^database decrypt failed$"):
        DatabaseDecryptor().decrypt(source, tmp_path / "out", SecretBytes(KEY))


def test_unsupported_page_shape_is_refused_without_output(tmp_path):
    from acquisition import SecretBytes
    from acquisition.decryptor import DatabaseFormatError, DatabaseDecryptor

    source = tmp_path / "encrypted"
    output = tmp_path / "out"
    source.write_bytes(bytes(PAGE - 1))
    with pytest.raises(DatabaseFormatError, match="^database format unsupported$"):
        DatabaseDecryptor().decrypt(source, output, SecretBytes(KEY))
    assert not output.exists()


def test_decrypt_streams_pages_before_reading_the_whole_source(monkeypatch, tmp_path):
    from acquisition import SecretBytes
    from acquisition.decryptor import DatabaseDecryptor

    encrypted, _ = _database()
    third_body = bytes(((index + 13) % 251 for index in range(PAGE - RESERVE)))
    source = tmp_path / "encrypted"
    output = tmp_path / "plaintext"
    source.write_bytes(encrypted + _encrypted_page(KEY, 3, third_body))
    original_open = Path.open

    class ObservingReader:
        def __init__(self, wrapped):
            self.wrapped = wrapped
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.wrapped.__exit__(*args)

        def read(self, size=-1):
            self.reads += 1
            if self.reads == 3:
                assert any(path.name.startswith(".") for path in tmp_path.iterdir())
            return self.wrapped.read(size)

    def observing_open(path, *args, **kwargs):
        opened = original_open(path, *args, **kwargs)
        return ObservingReader(opened) if path == source else opened

    monkeypatch.setattr(Path, "open", observing_open)
    DatabaseDecryptor().decrypt(source, output, SecretBytes(KEY))
    assert output.stat().st_size == PAGE * 3


def test_workspace_write_failure_is_not_misreported_as_decrypt_failure(
    monkeypatch, tmp_path
):
    from acquisition import SecretBytes
    from acquisition.decryptor import DatabaseDecryptor

    encrypted, _ = _database()
    source = tmp_path / "encrypted"
    output = tmp_path / "plaintext"
    source.write_bytes(encrypted)
    original_open = Path.open

    def failing_open(path, *args, **kwargs):
        if path != source and any("x" in value for value in args if isinstance(value, str)):
            raise OSError(28, "fixture workspace full")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    with pytest.raises(OSError, match="fixture workspace full"):
        DatabaseDecryptor().decrypt(source, output, SecretBytes(KEY))
    assert not output.exists()
