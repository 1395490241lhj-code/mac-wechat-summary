"""Comprehensive acceptance tests for the reconciled WeChat 4.1.15 credential model.

Covers Phase 1, Phase 7, Phase 8, and Phase 9:
- Multi-salt distinct raw keys
- Rejection of account secret by raw decryptor
- Rejection of mismatched raw keys across databases
- Real DatabaseKeyDeriver + real DatabaseDecryptor verification
- Operator 64-hex input validation and parsing
- End-to-end bootstrap with multi-salt source set
- New message shard with unseen salt read on-demand without re-bootstrap (Phase 7 gate)
- Security and failure handling (wrong secret, corrupted database, KeyStore rollback)
- Credential compatibility versioning (legacy raw-key profile vs account-passphrase profile)
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import pytest
from Crypto.Cipher import AES

import database_bootstrap as boot
from acquisition.contracts import AcquisitionState
from acquisition.coordinator import AcquisitionCoordinator
from acquisition.decryptor import (
    DatabaseDecryptor,
    DatabaseKeyError,
    KEY_SIZE,
    PAGE_SIZE,
    RESERVE_SIZE,
    SALT_SIZE,
)
from acquisition.deriver import DatabaseKeyDeriver, ACCOUNT_KDF_ITERATIONS
from acquisition.descriptor import (
    ACCOUNT_PASSPHRASE_PROFILE,
    ACCOUNT_SHARED_SECRET_V1,
    compatibility_token,
    descriptor_for,
    descriptors_for,
)
from acquisition.keystore import KeyDescriptor, SecretBytes
from acquisition.source_locator import SourceLocator

ACCOUNT_SECRET = b"synthetic-operator-passphrase-32"
ACCOUNT_SECRET_HEX = ACCOUNT_SECRET.hex()
SALT_SESSION = b"salt_session_001"
SALT_CONTACT = b"salt_contact_002"
SALT_MSG0 = b"salt_message0003"
SALT_MSG1 = b"salt_message1004"


def _mac_key(key: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha512", key, bytes(v ^ 0x3A for v in salt), 2, dklen=KEY_SIZE
    )


def _encrypt_page(raw_key: bytes, salt: bytes, page_number: int = 1) -> bytes:
    body = bytes((i % 251 for i in range(PAGE_SIZE - RESERVE_SIZE - SALT_SIZE)))
    iv = bytes([page_number % 256]) * 16
    encrypted = AES.new(raw_key, AES.MODE_CBC, iv).encrypt(body)
    prefix = salt + encrypted
    authenticated = prefix[SALT_SIZE:] + iv
    digest = hmac.new(_mac_key(raw_key, salt), authenticated, hashlib.sha512)
    digest.update(struct.pack("<I", page_number))
    return prefix + iv + digest.digest()


def _derive_kdf(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, ACCOUNT_KDF_ITERATIONS, dklen=KEY_SIZE)


class ReconciledDecryptor:
    """Decryptor that verifies each database's raw key was derived from account secret and salt."""

    def __init__(self, account_secret: bytes, roles_by_salt: dict[bytes, str]):
        self.account_secret = account_secret
        self.roles_by_salt = roles_by_salt

    def decrypt(self, source: Path, output: Path, secret: SecretBytes) -> None:
        salt = source.read_bytes()[:16]
        expected_raw_key = _derive_kdf(self.account_secret, salt)
        if secret.value != expected_raw_key:
            raise DatabaseKeyError()

        role = self.roles_by_salt.get(salt, "message")
        conn = sqlite3.connect(output)
        if role == "session":
            conn.execute("CREATE TABLE SessionTable (username TEXT)")
            conn.execute("INSERT INTO SessionTable VALUES ('wxid_test')")
        elif role == "contact":
            conn.execute("CREATE TABLE contact (username TEXT, remark TEXT, nick_name TEXT)")
            conn.execute("INSERT INTO contact VALUES ('wxid_test', 'Remark', 'Nickname')")
        else:
            conn.execute("CREATE TABLE Name2Id (user_name TEXT)")
            conn.execute("INSERT INTO Name2Id VALUES ('wxid_test')")
            digest = hashlib.md5(b"wxid_test").hexdigest()
            conn.execute(
                f"CREATE TABLE Msg_{digest} (local_id INTEGER, server_id INTEGER, local_type INTEGER, "
                "real_sender_id INTEGER, create_time INTEGER, message_content TEXT, source TEXT, packed_info_data TEXT)"
            )
            conn.execute(f"INSERT INTO Msg_{digest} VALUES (1, 101, 1, 1, 100, 'hello', '', '')")
        conn.commit()
        conn.close()


class InMemoryKeyStore:
    def __init__(self, initial: dict | None = None):
        self.data = dict(initial) if initial else {}

    def load(self, desc: KeyDescriptor) -> SecretBytes | None:
        return self.data.get(desc)

    def put(self, desc: KeyDescriptor, val: SecretBytes) -> None:
        self.data[desc] = val

    def delete(self, desc: KeyDescriptor) -> bool:
        return self.data.pop(desc, None) is not None


class StaticSecretProvider:
    def __init__(self, secret: bytes | None):
        self.secret = secret

    def acquire(self, fingerprint: str) -> SecretBytes | None:
        return SecretBytes(self.secret) if self.secret else None


# --- Phase 1: Mismatch Proof ---


def test_four_raw_database_keys_are_distinct():
    salts = [SALT_SESSION, SALT_CONTACT, SALT_MSG0, SALT_MSG1]
    keys = [_derive_kdf(ACCOUNT_SECRET, s) for s in salts]
    assert len(set(keys)) == 4
    for k in keys:
        assert len(k) == 32


def test_supplying_account_secret_directly_to_database_decryptor_fails(tmp_path):
    raw_key = _derive_kdf(ACCOUNT_SECRET, SALT_SESSION)
    enc_path = tmp_path / "session.db"
    enc_path.write_bytes(_encrypt_page(raw_key, SALT_SESSION))
    out_path = tmp_path / "out.db"

    decryptor = DatabaseDecryptor()
    with pytest.raises(DatabaseKeyError):
        decryptor.decrypt(enc_path, out_path, SecretBytes(ACCOUNT_SECRET))


def test_supplying_single_raw_database_key_to_other_database_fails(tmp_path):
    raw_key_session = _derive_kdf(ACCOUNT_SECRET, SALT_SESSION)
    raw_key_contact = _derive_kdf(ACCOUNT_SECRET, SALT_CONTACT)

    contact_enc = tmp_path / "contact.db"
    contact_enc.write_bytes(_encrypt_page(raw_key_contact, SALT_CONTACT))
    out_path = tmp_path / "out.db"

    decryptor = DatabaseDecryptor()
    decryptor.decrypt(contact_enc, out_path, SecretBytes(raw_key_contact))
    assert out_path.exists()
    out_path.unlink()

    with pytest.raises(DatabaseKeyError):
        decryptor.decrypt(contact_enc, out_path, SecretBytes(raw_key_session))


# --- Phase 9: Real Deriver + Real DatabaseDecryptor ---


def test_real_database_decryptor_with_derived_key_succeeds(tmp_path):
    deriver = DatabaseKeyDeriver()
    decryptor = DatabaseDecryptor()

    for s in [SALT_SESSION, SALT_CONTACT, SALT_MSG0, SALT_MSG1]:
        derived = deriver.derive(SecretBytes(ACCOUNT_SECRET), s)
        enc_file = tmp_path / f"{s.decode('latin1')}.db"
        out_file = tmp_path / f"{s.decode('latin1')}.out"
        enc_file.write_bytes(_encrypt_page(derived.value, s))

        decryptor.decrypt(enc_file, out_file, derived)
        assert out_file.exists()
        assert out_file.stat().st_size == PAGE_SIZE


# --- Phase 5: Operator Input Validation ---


def test_operator_input_format_validation():
    # Valid 64-hex lowercase
    valid_lower = "0" * 63 + "f"
    res = boot.parse_account_secret(valid_lower)
    assert res == bytes.fromhex(valid_lower)
    assert len(res) == 32

    # Valid 64-hex uppercase
    valid_upper = "A" * 64
    res = boot.parse_account_secret(valid_upper)
    assert res == bytes.fromhex(valid_upper)
    assert len(res) == 32

    # Leading/trailing whitespace is cleaned
    assert boot.parse_account_secret(f"  {valid_lower}\n  ") == bytes.fromhex(valid_lower)

    # Empty or None fails closed
    assert boot.parse_account_secret("") is None
    assert boot.parse_account_secret(None) is None
    assert boot.parse_account_secret("   ") is None

    # Invalid lengths fail closed
    assert boot.parse_account_secret("0" * 63) is None
    assert boot.parse_account_secret("0" * 65) is None
    assert boot.parse_account_secret("0" * 32) is None

    # Non-hex characters fail closed
    assert boot.parse_account_secret("z" * 64) is None
    assert boot.parse_account_secret("0" * 63 + "g") is None


# --- Phase 7 Acceptance Gate: Bootstrap + New Shard without re-bootstrap ---


def test_bootstrap_and_new_message_shard_derived_on_demand(tmp_path):
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    (root / "message" / "message_0.db").write_bytes(SALT_MSG0 + b"\x00" * 4080)

    roles = {
        SALT_SESSION: "session",
        SALT_CONTACT: "contact",
        SALT_MSG0: "message",
        SALT_MSG1: "message",
    }
    decryptor = ReconciledDecryptor(ACCOUNT_SECRET, roles)
    deriver = DatabaseKeyDeriver()
    store = InMemoryKeyStore()
    manifest = tmp_path / "database_source.json"

    # Step 1: Bootstrap once with account secret
    result = boot.bootstrap(
        root,
        secret_provider=StaticSecretProvider(ACCOUNT_SECRET),
        key_store=store,
        manifest_path=manifest,
        workspace_root=tmp_path / "workspaces",
        decryptor=decryptor,
        deriver=deriver,
    )
    assert result.state == boot.BOOTSTRAP_READY
    assert manifest.exists()

    # Verify no secret material stored in manifest
    manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
    manifest_str = json.dumps(manifest_doc)
    assert ACCOUNT_SECRET_HEX not in manifest_str
    assert "passphrase" not in manifest_str
    assert "secret" not in manifest_str

    # Step 2: Now a new shard message_1.db appears with an unseen salt (SALT_MSG1)
    (root / "message" / "message_1.db").write_bytes(SALT_MSG1 + b"\x00" * 4080)

    # Step 3: Fast Lane / Reader resolves source set and prepares without new bootstrap
    new_source_set = boot.candidate_source_set(root)
    assert len(new_source_set.message_sources) == 2

    coordinator = AcquisitionCoordinator(
        store,
        tmp_path / "workspaces",
        decryptor=decryptor,
        deriver=deriver,
    )

    with coordinator.prepare(new_source_set, database_mode_enabled=True) as outcome:
        assert outcome.readiness.state == AcquisitionState.READY
        prepared = outcome.prepared_source
        assert prepared is not None
        # Both message handles are present and decrypted cleanly
        assert len(prepared.message_handles) == 2


# --- Phase 8: Security and Failure Boundaries ---


def test_wrong_account_secret_fails_and_publishes_nothing(tmp_path):
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    (root / "message" / "message_0.db").write_bytes(SALT_MSG0 + b"\x00" * 4080)

    roles = {
        SALT_SESSION: "session",
        SALT_CONTACT: "contact",
        SALT_MSG0: "message",
    }
    decryptor = ReconciledDecryptor(ACCOUNT_SECRET, roles)
    store = InMemoryKeyStore()
    manifest = tmp_path / "database_source.json"

    # Operator supplies wrong secret
    wrong_secret = b"wrong-secret-that-does-not-mat32"
    result = boot.bootstrap(
        root,
        secret_provider=StaticSecretProvider(wrong_secret),
        key_store=store,
        manifest_path=manifest,
        workspace_root=tmp_path / "workspaces",
        decryptor=decryptor,
        deriver=DatabaseKeyDeriver(),
    )
    assert result.state == boot.BOOTSTRAP_SECRET_REJECTED
    assert not manifest.exists()
    assert len(store.data) == 0


def test_corrupted_database_fails_and_publishes_nothing(tmp_path):
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    # Corrupted contact.db has truncated salt
    (root / "contact" / "contact.db").write_bytes(b"short")
    (root / "message" / "message_0.db").write_bytes(SALT_MSG0 + b"\x00" * 4080)

    roles = {
        SALT_SESSION: "session",
        SALT_CONTACT: "contact",
        SALT_MSG0: "message",
    }
    decryptor = ReconciledDecryptor(ACCOUNT_SECRET, roles)
    store = InMemoryKeyStore()
    manifest = tmp_path / "database_source.json"

    result = boot.bootstrap(
        root,
        secret_provider=StaticSecretProvider(ACCOUNT_SECRET),
        key_store=store,
        manifest_path=manifest,
        workspace_root=tmp_path / "workspaces",
        decryptor=decryptor,
        deriver=DatabaseKeyDeriver(),
    )
    assert result.state == boot.BOOTSTRAP_SOURCE_INVALID
    assert not manifest.exists()
    assert len(store.data) == 0


def test_legacy_raw_key_records_cannot_be_loaded_by_account_passphrase_descriptors():
    fingerprint = "a" * 64
    legacy_compat = compatibility_token("messages", ACCOUNT_SHARED_SECRET_V1)
    passphrase_compat = compatibility_token("messages", ACCOUNT_PASSPHRASE_PROFILE)

    assert legacy_compat != passphrase_compat

    legacy_desc = descriptor_for(fingerprint, "messages", ACCOUNT_SHARED_SECRET_V1)
    passphrase_desc = descriptor_for(fingerprint, "messages", ACCOUNT_PASSPHRASE_PROFILE)

    assert legacy_desc != passphrase_desc
    assert legacy_desc.compatibility_token != passphrase_desc.compatibility_token

    # An old KeyStore containing only legacy raw-key profile records cannot answer an account-passphrase descriptor query
    store = InMemoryKeyStore({legacy_desc: SecretBytes(b"old-raw-database-key-32bytes-len")})
    assert store.load(passphrase_desc) is None


def test_custom_decryptor_without_custom_deriver_still_receives_derived_key(tmp_path):
    root = tmp_path / "source_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    salt_session = b"salt_session_001"
    salt_contact = b"salt_contact_002"
    salt_msg0 = b"salt_message0003"

    (root / "session" / "session.db").write_bytes(salt_session + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(salt_contact + b"\x00" * 4080)
    (root / "message" / "message_0.db").write_bytes(salt_msg0 + b"\x00" * 4080)

    received_keys = []
    class CustomSpyDecryptor:
        def decrypt(self, source: Path, output: Path, secret: SecretBytes) -> None:
            received_keys.append((source.name, secret.value))
            conn = sqlite3.connect(output)
            conn.execute("CREATE TABLE t (x TEXT)")
            conn.commit()
            conn.close()

    source_set = boot.candidate_source_set(root)
    descriptors = tuple(s.key_descriptor for s in source_set.ordered_sources())
    account_secret = b"synthetic-operator-passphrase-32"
    key_store = boot._CandidateSecrets(descriptors, SecretBytes(account_secret))

    # Inject custom decryptor, but OMIT deriver
    coordinator = AcquisitionCoordinator(
        key_store,
        tmp_path / "workspaces",
        decryptor=CustomSpyDecryptor(),
        # deriver is not passed!
    )
    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        pass

    assert len(received_keys) == 3
    salts_by_name = {
        "session.db": salt_session,
        "contact.db": salt_contact,
        "message_0.db": salt_msg0,
    }
    # Decryptor MUST receive derived per-database key, NEVER the raw account secret!
    for source_name, key in received_keys:
        assert key != account_secret, "Decryption bypassed derivation and received account secret directly!"
        # Find which salt matches this key
        expected_keys = {_derive_kdf(account_secret, s) for s in [salt_session, salt_contact, salt_msg0]}
        assert key in expected_keys

def test_production_path_without_refresh_misses_new_shard(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    (root / "message" / "message_0.db").write_bytes(SALT_MSG0 + b"\x00" * 4080)

    roles = {
        SALT_SESSION: "session",
        SALT_CONTACT: "contact",
        SALT_MSG0: "message",
        SALT_MSG1: "message",
    }
    decryptor = ReconciledDecryptor(ACCOUNT_SECRET, roles)
    store = InMemoryKeyStore()
    manifest = home / "Library" / "Application Support" / "WeChatCompanion" / "database_source.json"

    # Step 1-2: Bootstrap initial source set and publish real manifest
    res = boot.bootstrap(
        root,
        secret_provider=StaticSecretProvider(ACCOUNT_SECRET),
        key_store=store,
        manifest_path=manifest,
        workspace_root=home / "workspaces",
        decryptor=decryptor,
    )
    assert res.state == boot.BOOTSTRAP_READY

    # Step 3: Add message_1.db with unseen salt
    (root / "message" / "message_1.db").write_bytes(SALT_MSG1 + b"\x00" * 4080)

    # Step 4-5: Reopen through production composition path WITHOUT manual candidate_source_set
    from acquired_database_source import open_database_source
    source = open_database_source(
        lambda: None,
        home=home,
        key_store=store,
        decryptor=decryptor,
    )
    assert source is not None

    # Step 6: Verify that production path surfaces both message shards
    # Current un-reconciled code only has message_0.db from manifest, so this must be RED!
    assert len(source._source_set.message_sources) == 2, "Production open_database_source failed to include new message_1.db shard!"

def test_true_production_new_shard_acceptance(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    (root / "message" / "message_0.db").write_bytes(SALT_MSG0 + b"\x00" * 4080)

    roles = {
        SALT_SESSION: "session",
        SALT_CONTACT: "contact",
        SALT_MSG0: "message",
        SALT_MSG1: "message",
    }
    decryptor = ReconciledDecryptor(ACCOUNT_SECRET, roles)
    store = InMemoryKeyStore()
    manifest = home / "Library" / "Application Support" / "WeChatCompanion" / "database_source.json"

    # Step 1: Bootstrap initial source set and publish real manifest
    res = boot.bootstrap(
        root,
        secret_provider=StaticSecretProvider(ACCOUNT_SECRET),
        key_store=store,
        manifest_path=manifest,
        workspace_root=home / "workspaces",
        decryptor=decryptor,
    )
    assert res.state == boot.BOOTSTRAP_READY

    # Step 2: Later WeChat writes message_1.db with an unseen salt (SALT_MSG1)
    (root / "message" / "message_1.db").write_bytes(SALT_MSG1 + b"\x00" * 4080)

    # Step 3: Reopen through production path WITHOUT calling candidate_source_set()
    from acquired_database_source import open_database_source
    source = open_database_source(
        lambda: None,
        home=home,
        key_store=store,
        decryptor=decryptor,
    )
    assert source is not None

    # Step 4: Verify that bounded refresh surfaced both shards
    assert len(source._source_set.message_sources) == 2

    # Step 5: Verify Reader actually queries both shards and gets conversation items
    result = source.list_conversations(10)
    assert result.items
    assert len(result.items) >= 1


def test_bounded_refresh_shard_removal(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    msg0 = root / "message" / "message_0.db"
    msg1 = root / "message" / "message_1.db"
    msg0.write_bytes(SALT_MSG0 + b"\x00" * 4080)
    msg1.write_bytes(SALT_MSG1 + b"\x00" * 4080)

    roles = {
        SALT_SESSION: "session",
        SALT_CONTACT: "contact",
        SALT_MSG0: "message",
        SALT_MSG1: "message",
    }
    decryptor = ReconciledDecryptor(ACCOUNT_SECRET, roles)
    store = InMemoryKeyStore()
    manifest = home / "Library" / "Application Support" / "WeChatCompanion" / "database_source.json"

    res = boot.bootstrap(
        root,
        secret_provider=StaticSecretProvider(ACCOUNT_SECRET),
        key_store=store,
        manifest_path=manifest,
        workspace_root=home / "workspaces",
        decryptor=decryptor,
    )
    assert res.state == boot.BOOTSTRAP_READY

    # Remove message_1.db
    msg1.unlink()

    from acquired_database_source import open_database_source
    source = open_database_source(
        lambda: None,
        home=home,
        key_store=store,
        decryptor=decryptor,
    )
    assert source is not None
    assert len(source._source_set.message_sources) == 1
    assert source._source_set.message_sources[0].main == msg0


def test_bounded_refresh_wal_and_shm_dynamic_detection(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    msg0 = root / "message" / "message_0.db"
    msg0.write_bytes(SALT_MSG0 + b"\x00" * 4080)

    roles = {
        SALT_SESSION: "session",
        SALT_CONTACT: "contact",
        SALT_MSG0: "message",
    }
    decryptor = ReconciledDecryptor(ACCOUNT_SECRET, roles)
    store = InMemoryKeyStore()
    manifest = home / "Library" / "Application Support" / "WeChatCompanion" / "database_source.json"

    res = boot.bootstrap(
        root,
        secret_provider=StaticSecretProvider(ACCOUNT_SECRET),
        key_store=store,
        manifest_path=manifest,
        workspace_root=home / "workspaces",
        decryptor=decryptor,
    )
    assert res.state == boot.BOOTSTRAP_READY

    # Later a WAL and SHM companion appear for message_0.db
    wal = root / "message" / "message_0.db-wal"
    shm = root / "message" / "message_0.db-shm"
    wal.write_bytes(b"wal-bytes")
    shm.write_bytes(b"shm-bytes")

    from acquired_database_source import open_database_source
    source = open_database_source(
        lambda: None,
        home=home,
        key_store=store,
        decryptor=decryptor,
    )
    assert source is not None
    shard0 = source._source_set.message_sources[0]
    assert shard0.wal == wal
    assert shard0.shm == shm


def test_bounded_refresh_ignores_non_db_files(tmp_path):
    from acquisition.source_refresher import BoundedSourceRefresher
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    (root / "message" / "message_0.db").write_bytes(SALT_MSG0 + b"\x00" * 4080)

    # Place non-db artifacts
    (root / "message" / ".DS_Store").write_bytes(b"ds-store")
    (root / "message" / "notes.txt").write_bytes(b"text")
    (root / "message" / "subdir").mkdir()

    source_set = boot.candidate_source_set(root)
    refresher = BoundedSourceRefresher()
    refreshed = refresher.refresh(source_set)

    # Only message_0.db is included
    assert len(refreshed.message_sources) == 1
    assert refreshed.message_sources[0].main.name == "message_0.db"


def test_bounded_refresh_never_leaves_message_directory(tmp_path):
    from acquisition.source_refresher import BoundedSourceRefresher
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    (root / "message" / "message_0.db").write_bytes(SALT_MSG0 + b"\x00" * 4080)

    # Create a completely separate sibling account root with other databases
    other_root = tmp_path / "other_account"
    (other_root / "message").mkdir(parents=True)
    (other_root / "message" / "message_other.db").write_bytes(b"other" * 100)

    source_set = boot.candidate_source_set(root)
    refresher = BoundedSourceRefresher()
    refreshed = refresher.refresh(source_set)

    # Scans only the recorded message dir; other_root is never inspected
    assert len(refreshed.message_sources) == 1
    assert "other_account" not in str(refreshed.message_sources[0].main)


def test_session_or_contact_anchor_change_fails_closed(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    (root / "message" / "message_0.db").write_bytes(SALT_MSG0 + b"\x00" * 4080)

    roles = {
        SALT_SESSION: "session",
        SALT_CONTACT: "contact",
        SALT_MSG0: "message",
    }
    decryptor = ReconciledDecryptor(ACCOUNT_SECRET, roles)
    store = InMemoryKeyStore()
    manifest = home / "Library" / "Application Support" / "WeChatCompanion" / "database_source.json"

    res = boot.bootstrap(
        root,
        secret_provider=StaticSecretProvider(ACCOUNT_SECRET),
        key_store=store,
        manifest_path=manifest,
        workspace_root=home / "workspaces",
        decryptor=decryptor,
    )
    assert res.state == boot.BOOTSTRAP_READY

    # Tamper with session anchor on disk (salt changed)
    (root / "session" / "session.db").write_bytes(b"tampered_salt_01" + b"\x00" * 4080)

    from acquired_database_source import open_database_source
    source = open_database_source(
        lambda: None,
        home=home,
        key_store=store,
        decryptor=decryptor,
    )
    assert source is not None
    # Because session anchor changed, the database source is not safely readable
    status = source.status()
    assert status.ready is False


def test_derivation_profile_matches_compatibility_profile():
    deriver = DatabaseKeyDeriver()
    assert deriver.profile_id == ACCOUNT_PASSPHRASE_PROFILE
    assert str(ACCOUNT_KDF_ITERATIONS) in ACCOUNT_PASSPHRASE_PROFILE


def test_keystore_role_records_consistency(tmp_path):
    # Verify all 3 role descriptors are populated on bootstrap
    root = tmp_path / "wechat_root"
    (root / "session").mkdir(parents=True)
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir(parents=True)

    (root / "session" / "session.db").write_bytes(SALT_SESSION + b"\x00" * 4080)
    (root / "contact" / "contact.db").write_bytes(SALT_CONTACT + b"\x00" * 4080)
    (root / "message" / "message_0.db").write_bytes(SALT_MSG0 + b"\x00" * 4080)

    roles = {
        SALT_SESSION: "session",
        SALT_CONTACT: "contact",
        SALT_MSG0: "message",
    }
    decryptor = ReconciledDecryptor(ACCOUNT_SECRET, roles)
    store = InMemoryKeyStore()
    manifest = tmp_path / "manifest.json"

    res = boot.bootstrap(
        root,
        secret_provider=StaticSecretProvider(ACCOUNT_SECRET),
        key_store=store,
        manifest_path=manifest,
        workspace_root=tmp_path / "workspaces",
        decryptor=decryptor,
    )
    assert res.state == boot.BOOTSTRAP_READY
    assert len(store.data) == 3

    # All 3 records contain identical SecretBytes (account secret)
    for desc, sec in store.data.items():
        assert sec.value == ACCOUNT_SECRET

    # If one role record is deleted from KeyStore, coordinator must refuse the lease
    source_set = boot.candidate_source_set(root)
    session_desc = [d for d in store.data if d.role_token == "conversation_identity"][0]
    store.delete(session_desc)

    coordinator = AcquisitionCoordinator(
        store,
        tmp_path / "workspaces",
        decryptor=decryptor,
    )
    with coordinator.prepare(source_set, database_mode_enabled=True) as outcome:
        assert outcome.readiness.state == AcquisitionState.NEEDS_BOOTSTRAP
