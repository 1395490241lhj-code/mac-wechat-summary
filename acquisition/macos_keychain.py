"""Security.framework translation for the database KeyStore."""

from __future__ import annotations

from .keystore import KeyStoreError


SERVICE = "org.mac-wechat-summary.database-keys.v1"


class MacOSKeychain:
    def __init__(self, security: object | None = None) -> None:
        if security is None:
            try:
                import Security
            except ImportError:
                raise KeyStoreError("key store access failure") from None

            security = Security
        self._security = security

    def _query(self, account: str) -> dict:
        sec = self._security
        return {
            sec.kSecClass: sec.kSecClassGenericPassword,
            sec.kSecAttrService: SERVICE,
            sec.kSecAttrAccount: account,
            sec.kSecAttrSynchronizable: False,
        }

    def load(self, account: str) -> bytes | None:
        sec = self._security
        try:
            query = self._query(account)
            query.update({sec.kSecReturnData: True, sec.kSecMatchLimit: sec.kSecMatchLimitOne})
            status, result = sec.SecItemCopyMatching(query, None)
            if status == sec.errSecItemNotFound:
                return None
            if status != sec.errSecSuccess or result is None:
                raise KeyStoreError("key store access failure")
            value = bytes(result)
            if not value:
                raise KeyStoreError("key store access failure")
            return value
        except KeyStoreError:
            raise
        except Exception:
            raise KeyStoreError("key store access failure") from None

    def put(self, account: str, value: bytes) -> None:
        sec = self._security
        try:
            query = self._query(account)
            status, _ = sec.SecItemAdd({**query, sec.kSecValueData: value}, None)
            if status == sec.errSecSuccess:
                return
            if status == sec.errSecDuplicateItem:
                updated = sec.SecItemUpdate(query, {sec.kSecValueData: value})
                if updated == sec.errSecSuccess:
                    return
                if updated == sec.errSecItemNotFound:
                    raise KeyStoreError("key store duplicate update conflict")
            raise KeyStoreError("key store access failure")
        except KeyStoreError:
            raise
        except Exception:
            raise KeyStoreError("key store access failure") from None

    def delete(self, account: str) -> bool:
        sec = self._security
        try:
            status = sec.SecItemDelete(self._query(account))
            if status == sec.errSecItemNotFound:
                return False
            if status == sec.errSecSuccess:
                return True
            raise KeyStoreError("key store access failure")
        except KeyStoreError:
            raise
        except Exception:
            raise KeyStoreError("key store access failure") from None
