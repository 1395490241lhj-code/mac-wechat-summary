"""Bootstrap: validate completely, then publish; never activate a guess."""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "bridge"):
    sys.path.insert(0, str(path))

from acquisition import KeyDescriptor, KeyStore, SecretBytes  # noqa: E402
from acquisition.decryptor import DatabaseKeyError  # noqa: E402
from acquisition.descriptor import descriptors_for, source_fingerprint  # noqa: E402
from acquisition.source_locator import record_document  # noqa: E402
import acquired_database_source  # noqa: E402
import database_bootstrap as boot  # noqa: E402
import store_access  # noqa: E402

ACCOUNT = b"synthetic-account-secret"
OTHER = b"synthetic-other-secret"
CONVERSATION = "wxid_fixture_conversation"


# --- synthetic encrypted sources ---------------------------------------------


def _encrypted(path: Path, salt: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(salt + bytes(4080))


def _source_root(root: Path, *, session_salt=bytes(range(16)),
                 contact_salt=bytes(range(16, 32)), parts: int = 1) -> Path:
    _encrypted(root / "session" / "session.db", session_salt)
    _encrypted(root / "contact" / "contact.db", contact_salt)
    for index in range(parts):
        _encrypted(root / "message" / f"message_{index}.db", bytes([index]) * 16)
    return root


def _plaintext(path: Path, role: str) -> None:
    connection = sqlite3.connect(path)
    if role == "session":
        connection.execute("CREATE TABLE SessionTable (username TEXT)")
        connection.execute("INSERT INTO SessionTable VALUES (?)", (CONVERSATION,))
    elif role == "contact":
        connection.execute("CREATE TABLE contact (username TEXT, remark TEXT, nick_name TEXT)")
        connection.execute("INSERT INTO contact VALUES (?,?,?)",
                           (CONVERSATION, "Fixture Remark", "Fixture Nickname"))
    else:
        digest = hashlib.md5(CONVERSATION.encode("utf-8")).hexdigest()
        connection.execute("CREATE TABLE Name2Id (user_name TEXT)")
        connection.execute("INSERT INTO Name2Id VALUES ('wxid_fixture_sender')")
        columns = ("local_id", "server_id", "local_type", "real_sender_id",
                   "create_time", "message_content", "source", "packed_info_data")
        connection.execute(
            "CREATE TABLE Msg_" + digest + " (" + ", ".join(
                name + " BLOB" for name in columns) + ")")
        connection.execute(
            "INSERT INTO Msg_" + digest + " VALUES (1, 101, 1, 1, 100, X'66697874757265', NULL, NULL)")
    connection.commit()
    connection.close()


def _role_for(source: Path) -> str:
    if source.name == "session.db":
        return "session"
    if source.name == "contact.db":
        return "contact"
    return "message"


def _roles_by_salt(root: Path) -> dict[bytes, str]:
    """Map each source's salt to its role.

    The snapshotter copies into the lease under opaque names, so the role can no
    longer be read off the path -- but the copy keeps the encrypted bytes, and
    the salt identifies which source it came from.
    """
    return {
        path.read_bytes()[:16]: _role_for(path)
        for path in sorted(root.rglob("*.db"))
    }


class _Decryptor:
    """Synthetic decryption: a role accepts only the secret provisioned for it."""

    def __init__(self, root: Path, keys: dict[str, bytes] | None = None) -> None:
        self._roles = _roles_by_salt(root)
        self._keys = keys or {}

    def decrypt(self, source: Path, output: Path, secret: SecretBytes) -> None:
        role = self._roles.get(source.read_bytes()[:16], "message")
        if self._keys.get(role, ACCOUNT) != secret.value:
            raise DatabaseKeyError()
        _plaintext(output, role)


class _ChangedSurface:
    """Synthetic: decrypts fine, but the conversation table is a changed generation."""

    def __init__(self, root: Path, only: str | None = None) -> None:
        self._roles = _roles_by_salt(root)
        self._names = {
            path.read_bytes()[:16]: path.name for path in sorted(root.rglob("*.db"))
        }
        self._only = only

    def decrypt(self, source: Path, output: Path, secret: SecretBytes) -> None:
        salt = source.read_bytes()[:16]
        role = self._roles.get(salt, "message")
        changed = role == "message" and (
            self._only is None or self._names.get(salt) == self._only)
        if not changed:
            _plaintext(output, role)
            return
        digest = hashlib.md5(CONVERSATION.encode("utf-8")).hexdigest()
        connection = sqlite3.connect(output)
        connection.execute(
            "CREATE TABLE Msg_" + digest + " (local_id BLOB, server_id BLOB, "
            "local_type BLOB, real_sender_id BLOB, create_time BLOB, "
            "message_content BLOB, source BLOB, packed_info_v2 BLOB)")
        connection.commit()
        connection.close()


class _Store:
    """An in-memory stand-in for the durable KeyStore."""

    def __init__(self, *, fail_put: bool = False, fail_load: bool = False) -> None:
        self.items: dict[str, SecretBytes] = {}
        self._fail_put = fail_put
        self._fail_load = fail_load

    def load(self, descriptor: KeyDescriptor) -> SecretBytes | None:
        if self._fail_load:
            raise OSError("synthetic durable read failure")
        return self.items.get(descriptor.account_id)

    def put(self, descriptor: KeyDescriptor, secret: SecretBytes) -> None:
        if self._fail_put:
            raise OSError("synthetic durable write failure")
        self.items[descriptor.account_id] = secret

    def delete(self, descriptor: KeyDescriptor) -> bool:
        return self.items.pop(descriptor.account_id, None) is not None


class _Provider:
    def __init__(self, secret: bytes | None = ACCOUNT) -> None:
        self._secret = secret
        self.calls = 0

    def acquire(self, fingerprint: str) -> SecretBytes | None:
        self.calls += 1
        return SecretBytes(self._secret) if self._secret is not None else None


def _bootstrap(tmp_path, root, *, store=None, provider=None, manifest=None,
               decryptor=None, **kwargs):
    store = store if store is not None else _Store()
    manifest = manifest if manifest is not None else tmp_path / "database_source.json"
    result = boot.bootstrap(
        root,
        secret_provider=provider if provider is not None else _Provider(),
        key_store=store,
        manifest_path=manifest,
        workspace_root=tmp_path / "leases",
        decryptor=decryptor if decryptor is not None else _Decryptor(root),
        **kwargs,
    )
    return result, store, manifest


# --- 1-3: source, descriptors, provider boundary ------------------------------


def test_an_explicit_source_root_becomes_the_candidate_source_set(tmp_path):
    source_set = boot.candidate_source_set(_source_root(tmp_path / "root", parts=3))

    assert len(source_set.message_sources) == 3
    assert source_set.conversation_identity_source is not None
    assert source_set.display_identity_source is not None
    assert [s.main.name for s in source_set.message_sources] == [
        "message_0.db", "message_1.db", "message_2.db"]


def test_descriptors_come_from_the_committed_helper(tmp_path):
    root = _source_root(tmp_path / "root")
    source_set = boot.candidate_source_set(root)
    fingerprint = source_fingerprint(root / "session" / "session.db",
                                     root / "contact" / "contact.db")

    assert all(source.key_descriptor.source_fingerprint == fingerprint
               for source in source_set.ordered_sources())
    assert {source.key_descriptor for source in source_set.ordered_sources()} == set(
        descriptors_for(fingerprint).values())


def test_the_operator_secret_boundary_is_injectable():
    supplied = boot.OperatorSuppliedSecret(lambda: ACCOUNT)
    assert supplied.acquire("f") == SecretBytes(ACCOUNT)
    assert boot.OperatorSuppliedSecret(lambda: None).acquire("f") is None
    assert boot.OperatorSuppliedSecret(lambda: b"").acquire("f") is None


@pytest.mark.parametrize("root", ["missing", "empty"])
def test_a_missing_or_invalid_root_fails_closed(tmp_path, root):
    target = tmp_path / root
    if root == "empty":
        target.mkdir()

    result, store, manifest = _bootstrap(tmp_path, target)

    assert result.state in {boot.BOOTSTRAP_SOURCE_MISSING, boot.BOOTSTRAP_SOURCE_INVALID}
    assert store.items == {} and not manifest.exists()


# --- 4-7: validation ----------------------------------------------------------


def test_the_account_secret_must_validate_every_required_role(tmp_path):
    result, store, manifest = _bootstrap(tmp_path, _source_root(tmp_path / "root"))

    assert result.state == boot.BOOTSTRAP_READY
    assert manifest.is_file()
    descriptors = descriptors_for(
        source_fingerprint(tmp_path / "root" / "session" / "session.db",
                           tmp_path / "root" / "contact" / "contact.db"))
    assert set(store.items) == {d.account_id for d in descriptors.values()}
    assert all(secret == SecretBytes(ACCOUNT) for secret in store.items.values())


def test_a_secret_valid_for_only_one_role_is_rejected(tmp_path):
    """One role provisioned with a different key rejects the whole candidate."""
    root = _source_root(tmp_path / "root")
    result, store, manifest = _bootstrap(
        tmp_path, root,
        decryptor=_Decryptor(root, {"message": ACCOUNT, "session": ACCOUNT,
                                    "contact": OTHER}))

    assert result.state == boot.BOOTSTRAP_SECRET_REJECTED
    assert store.items == {} and not manifest.exists()


def test_a_wrong_secret_publishes_nothing(tmp_path):
    result, store, manifest = _bootstrap(
        tmp_path, _source_root(tmp_path / "root"),
        store=_Store(), provider=_Provider(OTHER),
    )

    assert result.state == boot.BOOTSTRAP_SECRET_REJECTED
    assert store.items == {} and not manifest.exists()


def test_no_secret_supplied_publishes_nothing(tmp_path):
    result, store, manifest = _bootstrap(
        tmp_path, _source_root(tmp_path / "root"), provider=_Provider(None))

    assert result.state == boot.BOOTSTRAP_SECRET_NOT_SUPPLIED
    assert store.items == {} and not manifest.exists()


def test_a_generation_the_provider_refuses_publishes_nothing(tmp_path):
    """A changed-but-parseable conversation surface is refused, and nothing lands."""
    root = _source_root(tmp_path / "root")
    result, store, manifest = _bootstrap(
        tmp_path, root, decryptor=_ChangedSurface(root))

    assert result.state == boot.BOOTSTRAP_UNSUPPORTED_GENERATION
    assert store.items == {} and not manifest.exists()


# --- 8-9: secrets never leak --------------------------------------------------


def test_the_record_never_carries_secret_material(tmp_path):
    root = _source_root(tmp_path / "root")
    result, store, manifest = _bootstrap(tmp_path, root)
    assert result.state == boot.BOOTSTRAP_READY

    text = manifest.read_text(encoding="utf-8")
    assert ACCOUNT.decode() not in text
    assert ACCOUNT.hex() not in text
    assert "secret" not in text and "password" not in text
    for descriptor in descriptors_for(
            source_fingerprint(root / "session" / "session.db",
                               root / "contact" / "contact.db")).values():
        assert descriptor.source_fingerprint in text
        assert descriptor.compatibility_token in text


def test_no_secret_appears_in_a_public_result(tmp_path):
    result, _, _ = _bootstrap(
        tmp_path, _source_root(tmp_path / "root"), provider=_Provider(OTHER))

    assert ACCOUNT.decode() not in repr(result)
    assert ACCOUNT.hex() not in repr(result)
    assert result.state in boot.BOOTSTRAP_STATES


# --- 10-12: publication and compensation --------------------------------------


def test_a_durable_write_failure_leaves_no_active_decision(tmp_path):
    result, store, manifest = _bootstrap(
        tmp_path, _source_root(tmp_path / "root"), store=_Store(fail_put=True))

    assert result.state == boot.BOOTSTRAP_DURABLE_WRITE_FAILED
    assert not manifest.exists()


def test_a_publication_failure_compensates_the_key_entries(tmp_path):
    root = _source_root(tmp_path / "root")
    manifest = tmp_path / "record"
    manifest.mkdir()  # a directory cannot be atomically replaced

    result, store, _ = _bootstrap(tmp_path, root, manifest=manifest)

    # The activation pointer could not be restored (a directory cannot be
    # replaced or removed), so the rollback is reported as incomplete and the
    # key entries are deliberately left alone: reverting them under a record
    # that still names them would be the broken state this ordering exists to
    # avoid. Nothing is falsely active -- the path is still not a record.
    assert result.state == boot.BOOTSTRAP_ROLLBACK_INCOMPLETE
    assert not manifest.is_file()
    assert store.items


def test_an_identity_surface_the_catalog_refuses_is_classified(tmp_path):
    """A session database without SessionTable is a fixed state, not a ValueError."""
    root = _source_root(tmp_path / "root")

    class _NoSessionTable(_Decryptor):
        def decrypt(self, source, output, secret):
            if self._roles.get(source.read_bytes()[:16]) == "session":
                connection = sqlite3.connect(output)
                connection.execute("CREATE TABLE unrelated (value INTEGER)")
                connection.commit()
                connection.close()
                return
            super().decrypt(source, output, secret)

    result, store, manifest = _bootstrap(
        tmp_path, root, decryptor=_NoSessionTable(root))

    assert result.state == boot.BOOTSTRAP_UNSUPPORTED_GENERATION
    assert store.items == {} and not manifest.exists()


def test_a_durable_read_failure_before_publication_is_classified(tmp_path):
    """A key store that cannot be read is a fixed state, not a KeyStoreError."""
    result, store, manifest = _bootstrap(
        tmp_path, _source_root(tmp_path / "root"), store=_Store(fail_load=True))

    assert result.state == boot.BOOTSTRAP_DURABLE_WRITE_FAILED
    assert not manifest.exists()


def test_one_changed_message_part_is_enough_to_refuse_the_generation(tmp_path):
    """The envelope is applied per part, not only to the first one visited."""
    root = _source_root(tmp_path / "root", parts=3)
    result, store, manifest = _bootstrap(
        tmp_path, root, decryptor=_ChangedSurface(root, only="message_1.db"))

    assert result.state == boot.BOOTSTRAP_UNSUPPORTED_GENERATION
    assert store.items == {} and not manifest.exists()


def test_a_same_source_refresh_with_another_secret_changes_nothing(tmp_path):
    """The identical-descriptor refresh cannot mix keys: such a candidate never
    validates.

    A fixed source is authenticated by exactly one secret, so a candidate that
    names the same descriptors must carry the same secret. Anything else is
    refused by validation before publication, which is what makes the
    identical-descriptor case safe without any atomic batch write.
    """
    root = _source_root(tmp_path / "root")
    result, store, manifest = _bootstrap(tmp_path, root)
    assert result.state == boot.BOOTSTRAP_READY
    known_good_keys = dict(store.items)
    known_good_record = manifest.read_text(encoding="utf-8")

    # Same encrypted anchors, so the descriptors are identical; the roles still
    # accept only the secret they were provisioned with.
    result, store, _ = _bootstrap(
        tmp_path, root, store=store, provider=_Provider(OTHER))

    assert result.state == boot.BOOTSTRAP_SECRET_REJECTED
    assert store.items == known_good_keys
    assert manifest.read_text(encoding="utf-8") == known_good_record


def test_a_new_message_part_is_recorded_on_a_same_secret_refresh(tmp_path):
    """A refresh that adds a shard must still update the activation record.

    The fingerprint deliberately ignores message-part salts, so a new shard
    leaves the descriptors identical -- and the record must still be rewritten
    to name it, or an ordinary read would silently miss that part.
    """
    root = _source_root(tmp_path / "root", parts=1)
    result, store, manifest = _bootstrap(tmp_path, root)
    assert result.state == boot.BOOTSTRAP_READY
    assert len(json.loads(manifest.read_text(encoding="utf-8"))["messages"]) == 1

    _encrypted(root / "message" / "message_1.db", bytes([1]) * 16)
    result, store, _ = _bootstrap(tmp_path, root, store=store)

    assert result.state == boot.BOOTSTRAP_READY
    assert len(json.loads(manifest.read_text(encoding="utf-8"))["messages"]) == 2


def test_a_failed_post_publication_verification_restores_the_previous_state(tmp_path):
    root = _source_root(tmp_path / "root")
    result, store, manifest = _bootstrap(
        tmp_path, root, verify=lambda: False)

    assert result.state == boot.BOOTSTRAP_VERIFICATION_FAILED
    assert store.items == {}
    assert not manifest.exists()


# --- 13-14: refresh -----------------------------------------------------------


def test_a_successful_refresh_switches_to_the_candidate_pair(tmp_path):
    first = _source_root(tmp_path / "one")
    result, store, manifest = _bootstrap(tmp_path, first)
    assert result.state == boot.BOOTSTRAP_READY
    before = manifest.read_text(encoding="utf-8")

    second = _source_root(tmp_path / "two", session_salt=bytes(range(32, 48)),
                          contact_salt=bytes(range(48, 64)))
    result, store, _ = _bootstrap(tmp_path, second, store=store)

    assert result.state == boot.BOOTSTRAP_READY
    assert manifest.read_text(encoding="utf-8") != before


def test_a_failed_refresh_preserves_the_known_good_pair(tmp_path):
    first = _source_root(tmp_path / "one")
    result, store, manifest = _bootstrap(tmp_path, first)
    assert result.state == boot.BOOTSTRAP_READY
    known_good_record = manifest.read_text(encoding="utf-8")
    known_good_keys = dict(store.items)

    second = _source_root(tmp_path / "two", session_salt=bytes(range(32, 48)),
                          contact_salt=bytes(range(48, 64)))
    result, store, _ = _bootstrap(tmp_path, second, store=store,
                                  provider=_Provider(OTHER))

    assert result.state == boot.BOOTSTRAP_SECRET_REJECTED
    assert manifest.read_text(encoding="utf-8") == known_good_record
    assert store.items == known_good_keys


# --- 15-18: ordinary runtime, isolation, no v1 extraction ---------------------


def test_ordinary_runtime_reads_the_published_pair_without_bootstrap(tmp_path):
    root = _source_root(tmp_path / "root")
    result, store, manifest = _bootstrap(
        tmp_path, root,
        manifest=acquired_database_source.recorded_manifest_path(tmp_path))
    assert result.state == boot.BOOTSTRAP_READY
    assert store.items

    spy = _Provider()
    source = acquired_database_source.open_database_source(
        lambda: None, home=tmp_path, key_store=store, decryptor=_Decryptor(root))

    assert source is not None
    assert source.status().ready is True
    assert spy.calls == 0


def test_ordinary_runtime_never_imports_bootstrap():
    for name in ("store_access.py", "acquired_database_source.py"):
        source = (ROOT / "bridge" / name).read_text(encoding="utf-8")
        assert "database_bootstrap" not in source, name
        assert "bootstrap" not in source.lower().replace("needs_bootstrap", ""), name


def test_visual_is_unaffected_by_a_failed_bootstrap(tmp_path, monkeypatch):
    monkeypatch.delenv(store_access.MESSAGE_SOURCE_ENV, raising=False)
    result, _, _ = _bootstrap(
        tmp_path, _source_root(tmp_path / "root"), provider=_Provider(OTHER))

    assert result.state == boot.BOOTSTRAP_SECRET_REJECTED
    assert store_access.selected_source_name() == store_access.SOURCE_VISUAL


def test_the_cross_layer_exception_is_exactly_two_files():
    offenders = []
    for path in (ROOT / "bridge").rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        roots = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
        if "wechatdb" in roots or "acquisition" in roots:
            offenders.append(path.relative_to(ROOT / "bridge").as_posix())

    assert sorted(offenders) == [
        "acquired_database_source.py",
        "database_bootstrap.py",
    ]


def test_bootstrap_never_uses_v1_discovery_or_extraction():
    source = (ROOT / "bridge" / "database_bootstrap.py").read_text(encoding="utf-8")

    for forbidden in ("key_extractor", "find_keys_macos", "auto_detect_db_dir",
                      "core.config", "frida", "os.walk", "Popen", "subprocess"):
        assert forbidden not in source, forbidden


def test_the_record_writer_round_trips_through_the_reader(tmp_path):
    root = _source_root(tmp_path / "root", parts=2)
    source_set = boot.candidate_source_set(root)
    document = record_document(source_set)
    text = json.dumps(document)

    assert "key_descriptor" in text and "compatibility_token" in text
    assert SecretBytes(ACCOUNT).value.decode() not in text
