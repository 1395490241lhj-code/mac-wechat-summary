# DB Reader macOS Keychain Gate

> This gate used disposable random material only. No WeChat key, account
> identity, source path or database content was used or recorded.

## Scope

| | |
|---|---|
| P2-B implementation | `0737b32` — `feat(v2): add macOS database key store` |
| Backend | macOS Security.framework through PyObjC |
| Live material | random disposable 32-byte values |
| Real WeChat key | no |
| Runtime wiring | none |

## Independent regression

After independent source review:

| Suite | Result |
|---|---:|
| acquisition | 57 passed |
| full repository | 959 passed |
| `git diff --check` | clean |

The implementation keeps the P2-A contract separate from the KeyStore, uses a
dedicated database-key service namespace, places raw bytes only in
`kSecValueData`, requests non-synchronizing items, performs duplicate replacement
through `SecItemUpdate`, and does not use the legacy subprocess-based
`core/keychain.py` path.

## Bounded live gate

A fresh random descriptor and two fresh random 32-byte values were used against
the actual local macOS Keychain.

| Check | Result |
|---|---|
| create / first round-trip | PASS |
| in-place replacement / second round-trip | PASS |
| delete existing item | PASS |
| load after delete returns missing | PASS |
| second delete returns false | PASS |
| residual disposable item after gate | none |

No secret value was printed or written to the repository.

## Result

**P2-B KEYSTORE LIVE GATE = MET.**

This proves the bounded KeyStore behavior only. It does not prove real WeChat
key acquisition, database snapshotting, WAL replay, decryption, bootstrap,
version compatibility or runtime source wiring.

The next implementation workstream is the approved acquisition Fast Lane:
private stable snapshot + encrypted WAL replay + ephemeral decrypt workspace +
a coordinator that produces explicit prepared handles for the already-proven
IdentityCatalog and ShardedMessageProvider.
