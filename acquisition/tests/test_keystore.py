"""Synthetic KeyStore and Security.framework translation tests."""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
FINGERPRINT = "a" * 64
COMPATIBILITY = "b" * 64
SECRET = bytes((0, 255)) + b"synthetic-key"


class FakeSecurity:
    kSecClass = "class"
    kSecClassGenericPassword = "generic-password"
    kSecAttrService = "service"
    kSecAttrAccount = "account"
    kSecAttrSynchronizable = "synchronizable"
    kSecValueData = "value-data"
    kSecReturnData = "return-data"
    kSecMatchLimit = "match-limit"
    kSecMatchLimitOne = "one"
    errSecSuccess = 0
    errSecItemNotFound = -25300
    errSecDuplicateItem = -25299

    def __init__(self):
        self.calls = []
        self.add_status = 0
        self.update_status = 0
        self.load_status = -25300
        self.load_data = None
        self.delete_status = -25300

    def SecItemAdd(self, query, result):
        self.calls.append(("add", dict(query), result))
        return self.add_status, None

    def SecItemCopyMatching(self, query, result):
        self.calls.append(("load", dict(query), result))
        return self.load_status, self.load_data

    def SecItemUpdate(self, query, attributes):
        self.calls.append(("update", dict(query), dict(attributes)))
        return self.update_status

    def SecItemDelete(self, query):
        self.calls.append(("delete", dict(query)))
        return self.delete_status


@pytest.fixture
def store():
    from acquisition import KeyDescriptor, KeyStore
    from acquisition.macos_keychain import MacOSKeychain

    native = FakeSecurity()
    descriptor = KeyDescriptor(FINGERPRINT, "messages", 1, COMPATIBILITY)
    return KeyStore(MacOSKeychain(native)), native, descriptor


def test_secret_bytes_accepts_only_nonempty_bytes_and_hides_value():
    from acquisition import SecretBytes

    for invalid in (None, "abc", bytearray(b"abc"), b""):
        with pytest.raises(ValueError, match="^secret bytes invalid$"):
            SecretBytes(invalid)
    secret = SecretBytes(SECRET)
    assert secret.value == SECRET
    assert "synthetic-key" not in repr(secret)
    assert "synthetic-key" not in str(secret)
    with pytest.raises(FrozenInstanceError):
        secret.value = b"other"


def test_descriptor_is_opaque_immutable_and_deterministic():
    from acquisition import KeyDescriptor

    descriptor = KeyDescriptor(FINGERPRINT, "messages", 1, COMPATIBILITY)
    assert [f.name for f in fields(descriptor)] == [
        "source_fingerprint", "role_token", "record_format_version",
        "compatibility_token",
    ]
    assert descriptor.account_id == KeyDescriptor(
        FINGERPRINT, "messages", 1, COMPATIBILITY
    ).account_id
    assert descriptor.account_id != KeyDescriptor(
        "c" * 64, "messages", 1, COMPATIBILITY
    ).account_id
    assert SECRET.hex() not in descriptor.account_id
    with pytest.raises(FrozenInstanceError):
        descriptor.role_token = "other"


@pytest.mark.parametrize("fingerprint", ["/Users/alice", "A" * 64, "f" * 63, "g" * 64])
def test_descriptor_rejects_non_digest_fingerprint(fingerprint):
    from acquisition import KeyDescriptor

    with pytest.raises(ValueError, match="^key descriptor invalid$"):
        KeyDescriptor(fingerprint, "messages", 1, COMPATIBILITY)


@pytest.mark.parametrize("role", ["", "../messages", "user@foo", "UPPER", "a" * 33])
def test_descriptor_rejects_unsafe_role(role):
    from acquisition import KeyDescriptor

    with pytest.raises(ValueError, match="^key descriptor invalid$"):
        KeyDescriptor(FINGERPRINT, role, 1, COMPATIBILITY)


@pytest.mark.parametrize("version,compatibility", [(0, COMPATIBILITY), (True, COMPATIBILITY), (1, "username"), (1, "C" * 64)])
def test_descriptor_rejects_invalid_format_or_compatibility(version, compatibility):
    from acquisition import KeyDescriptor

    with pytest.raises(ValueError, match="^key descriptor invalid$"):
        KeyDescriptor(FINGERPRINT, "messages", version, compatibility)


def test_missing_load_and_delete_are_normal(store):
    key_store, native, descriptor = store
    assert key_store.load(descriptor) is None
    assert key_store.delete(descriptor) is False
    assert [call[0] for call in native.calls] == ["load", "delete"]


def test_add_puts_only_raw_bytes_in_value_data_and_disables_sync(store):
    from acquisition.macos_keychain import SERVICE
    from acquisition import SecretBytes

    key_store, native, descriptor = store
    key_store.put(descriptor, SecretBytes(SECRET))
    name, query, result = native.calls[0]
    assert name == "add" and result is None
    assert query == {
        native.kSecClass: native.kSecClassGenericPassword,
        native.kSecAttrService: SERVICE,
        native.kSecAttrAccount: descriptor.account_id,
        native.kSecAttrSynchronizable: False,
        native.kSecValueData: SECRET,
    }
    assert SERVICE != "wechat-summary"
    assert SECRET not in tuple(query.values())[:-1]


def test_load_requests_data_for_exact_nonsync_item_and_wraps_bytes(store):
    key_store, native, descriptor = store
    native.load_status = 0
    native.load_data = SECRET
    assert key_store.load(descriptor).value == SECRET
    name, query, result = native.calls[0]
    assert name == "load" and result is None
    assert query == {
        native.kSecClass: native.kSecClassGenericPassword,
        native.kSecAttrService: "org.mac-wechat-summary.database-keys.v1",
        native.kSecAttrAccount: descriptor.account_id,
        native.kSecAttrSynchronizable: False,
        native.kSecReturnData: True,
        native.kSecMatchLimit: native.kSecMatchLimitOne,
    }


def test_duplicate_put_updates_existing_item_without_delete(store):
    from acquisition import SecretBytes

    key_store, native, descriptor = store
    native.add_status = native.errSecDuplicateItem
    key_store.put(descriptor, SecretBytes(SECRET))
    assert [call[0] for call in native.calls] == ["add", "update"]
    _, query, attributes = native.calls[1]
    assert query == {
        native.kSecClass: native.kSecClassGenericPassword,
        native.kSecAttrService: "org.mac-wechat-summary.database-keys.v1",
        native.kSecAttrAccount: descriptor.account_id,
        native.kSecAttrSynchronizable: False,
    }
    assert attributes == {native.kSecValueData: SECRET}


def test_failed_update_never_deletes_old_item(store):
    from acquisition import KeyStoreError, SecretBytes

    key_store, native, descriptor = store
    native.add_status = native.errSecDuplicateItem
    native.update_status = native.errSecItemNotFound
    with pytest.raises(KeyStoreError, match="^key store duplicate update conflict$"):
        key_store.put(descriptor, SecretBytes(SECRET))
    assert [call[0] for call in native.calls] == ["add", "update"]


def test_delete_targets_exact_nonsync_item(store):
    key_store, native, descriptor = store
    native.delete_status = 0
    assert key_store.delete(descriptor) is True
    assert native.calls == [("delete", {
        native.kSecClass: native.kSecClassGenericPassword,
        native.kSecAttrService: "org.mac-wechat-summary.database-keys.v1",
        native.kSecAttrAccount: descriptor.account_id,
        native.kSecAttrSynchronizable: False,
    })]


@pytest.mark.parametrize("operation", ["load", "put", "delete"])
def test_native_failures_have_fixed_content_free_error(store, operation):
    from acquisition import KeyStoreError, SecretBytes

    key_store, native, descriptor = store
    setattr(native, {"load": "load_status", "put": "add_status", "delete": "delete_status"}[operation], -9999)
    with pytest.raises(KeyStoreError, match="^key store access failure$") as error:
        if operation == "put":
            key_store.put(descriptor, SecretBytes(SECRET))
        else:
            getattr(key_store, operation)(descriptor)
    assert FINGERPRINT not in repr(error.value)
    assert "synthetic-key" not in repr(error.value)


def test_native_exception_is_sanitized(store):
    from acquisition import KeyStoreError

    key_store, native, descriptor = store
    native.SecItemDelete = lambda query: (_ for _ in ()).throw(RuntimeError("synthetic-key"))
    with pytest.raises(KeyStoreError, match="^key store access failure$") as error:
        key_store.delete(descriptor)
    assert error.value.__cause__ is None
    assert "synthetic-key" not in repr(error.value)


def test_key_store_error_vocabulary_is_closed():
    from acquisition import KeyStoreError

    with pytest.raises(ValueError, match="^key store reason invalid$"):
        KeyStoreError("synthetic-key")


@pytest.mark.parametrize("payload", [None, b""])
def test_malformed_success_payload_fails_closed(store, payload):
    from acquisition import KeyStoreError

    key_store, native, descriptor = store
    native.load_status = 0
    native.load_data = payload
    with pytest.raises(KeyStoreError, match="^key store access failure$"):
        key_store.load(descriptor)


def test_package_import_and_construction_do_not_perform_keychain_operation(store):
    from acquisition import KeyStore
    from acquisition.macos_keychain import MacOSKeychain

    _, native, _ = store
    KeyStore(MacOSKeychain(native))
    assert native.calls == []


def test_architecture_and_dependency_guards():
    for name in ("keystore.py", "macos_keychain.py"):
        source = (ROOT / "acquisition" / name).read_text()
        tree = ast.parse(source)
        roots = {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module and node.level == 0}
        roots |= {alias.name.split(".")[0] for node in ast.walk(tree)
                  if isinstance(node, ast.Import) for alias in node.names}
        assert roots.isdisjoint({"core", "bridge", "memory", "wechatdb", "subprocess", "requests", "socket", "urllib", "json", "base64", "Crypto", "ctypes", "frida"})
    assert "pyobjc-framework-Security>=10.0" in (ROOT / "requirements.txt").read_text()
    assert "pyobjc-framework-Security>=10.0" in (ROOT / "setup.py").read_text()
    assert "pyobjc-framework-Cocoa>=10.0" in (ROOT / "requirements.txt").read_text()
    assert "pyobjc-framework-Cocoa>=10.0" in (ROOT / "setup.py").read_text()
