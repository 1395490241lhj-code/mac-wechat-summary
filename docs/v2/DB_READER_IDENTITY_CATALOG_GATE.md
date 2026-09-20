# DB Reader IdentityCatalog Real Gate

> This gate records structural identity evidence only. It contains no usernames,
> contact names, conversation names, table digests, message content, keys, salts,
> account paths, or temporary evidence paths.

## 1. Gate identity

| | |
|---|---|
| Scope | provider-local `IdentityCatalog` real-evidence gate |
| Implementation | `8c10781` — `feat(wechatdb): build identity catalog from explicit sources` |
| Product decision | P1 MET under D-033 |
| Real multi-part prerequisite | G2 MET / P3 MET under D-032 |
| Runtime wiring | none |
| Acquisition implementation | none |

The catalog was built only from explicitly supplied read-only identity/message
handles and then handed to the existing `IdentityResolver` and
`ShardedMessageProvider`.

## 2. Synthetic regression state

Independent review re-ran the complete regression set after `8c10781`:

| Suite | Passed |
|---|---:|
| IdentityCatalog | 16 |
| provider | 173 |
| wechatdb | 217 |
| bridge | 179 |
| memory | 348 |
| shadow | 158 |
| Memory layering | 8 |

`git diff --check` was clean.

The implementation remained provider-local, used only explicit supplied roles,
and added no product wiring, acquisition, filesystem discovery, process access,
decryption or source-selection behavior.

## 3. Real source contribution evidence

The independently established real target contained 86 conversation identities.

| Identity source | Conversation digests covered |
|---|---:|
| session source only | 85 / 86 |
| contact source only | 85 / 86 |
| message-part `Name2Id` evidence only | 86 / 86 |
| session + contact | 85 / 86 |
| full catalog | **86 / 86** |

The full catalog left **0 unresolved conversation digests**.

The catalog snapshot contained:

| Evidence type | Count |
|---|---:|
| source-authored username/session mappings | 8,638 |
| display-name candidates | 8,888 |
| remark candidates | 325 |
| nickname candidates | 8,563 |

No individual value is recorded in this gate.

## 4. Provider integration

The full catalog was converted through `catalog.resolver()` and supplied to the
existing real seven-part `ShardedMessageProvider`.

| Check | Result |
|---|---:|
| conversations listed | 86 |
| source-authored conversation identities | **86 / 86** |
| parser fallback titles | **0** |
| conversation digest correspondence | 86 / 86 |
| canonical `conversation_identifier` correspondence | 86 / 86 |
| coverage | `observed_complete / full_window_observed` |
| readable parts | 7 |
| unknown / unavailable parts | 0 / 0 |
| unresolved identity diagnostics | 0 |

Therefore `IdentityCatalog` is not merely populated: its evidence reaches the
existing parser/provider flow and eliminates the parser fallback for every
conversation in the bounded real snapshot.

## 5. Sender display-name integration

The same 1,000 recent public messages were read once with an empty resolver and
once with the full real catalog resolver. Public message identity was used only
to align the two result sets.

| Check | Result |
|---|---:|
| messages compared | 1,000 |
| sender display values changed by catalog evidence | 990 |
| unresolved identity diagnostics | 0 |

This verifies that contact remark/nickname evidence reaches the existing
`IdentityResolver` / parser display-name path without changing coverage.

## 6. Gate result

**REAL IDENTITY CATALOG GATE = MET.**

The following statements are now supported for the bounded current-version real
snapshot:

- conversation/session identity can be recovered for all observed conversations;
- the provider no longer needs its `msg_<digest>` fallback for those conversations;
- contact display-name evidence reaches the existing resolver/parser path;
- identity resolution remains separate from coverage;
- no new product boundary or identity precedence implementation was introduced.

This gate does **not** satisfy P2, P4 or P5 and does not authorize wiring.

## 7. Next gate

The next bounded product decision is **P2: standing acquisition**.

That design must separately address explicit consent, secure key storage,
WAL-aware consistent snapshots, temporary plaintext lifecycle, bootstrap/key
refresh behavior, failure isolation and version compatibility before any runtime
wiring is attempted.
