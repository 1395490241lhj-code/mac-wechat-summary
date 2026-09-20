# DB Reader Real Multi-Part Gate — G2

> **G2 BEING GREEN IS NOT PRODUCTION PROMOTION.**
>
> G2 establishes scoped real-container evidence for the isolated candidate
> provider. It does not wire the provider, add standing acquisition, change the
> visual default, or satisfy the remaining promotion gates.

## 1. Gate identity

| | |
|---|---|
| Gate | G2 — real multi-part reader correctness gate |
| Branch | `feature/hermes-validation-isolation` (unmerged) |
| Corrected provider HEAD | `8b4250a` |
| Evidence scope | one operator-owned current Mac WeChat 4.1.15 multi-part message snapshot |
| Content policy | structural counts/status only; no message text, names, table digests, keys, salts, or account paths recorded |

D-032 authorizes this bounded real-evidence verification. D-030 remains lapsed.
No acquisition implementation or shipped dependency is introduced by this gate.

## 2. Preconditions

Before G2:

- G1 synthetic implementation gate was MET at `0ebcecc`;
- R0 post-G1 evidence decision was recorded at `7d4c1c1`;
- R1 real-evidence correctness correction landed at `c80ea2c`;
- independent review found one mixed-conversation sequence-scope defect;
- the sealing correction landed at `8b4250a`;
- the provider remained isolated, unregistered and unwired.

The independent sealing review also re-ran the previously failing real
`get_recent_messages` probe: it returned normally after `8b4250a`, with seven
readable parts, globally unique message ids and unique
`(conversation_id, sequence)` pairs.

## 3. G2 evidence

### G2-1 — real inventory

The seven actual message parts were probed through the corrected Discovery:

| Measure | Result |
|---|---:|
| readable | 7 |
| unavailable | 0 |
| unknown | 0 |
| readable empty parts | 3 |

The three valid empty message parts carried no invented bounds.

**G2-1: PASS.**

### G2-2 — complete conversation listing

With a caller limit above the actual conversation count:

| Measure | Result |
|---|---:|
| conversations returned | 86 |
| coverage status | `observed_complete` |
| coverage reason | `full_window_observed` |
| diagnostics readable | 7 |
| diagnostics unavailable | 0 |
| diagnostics unknown | 0 |

No valid empty part produced `partial_inventory`.

**G2-2: PASS.**

### G2-3 — real multi-part overlap exists

Independent structural inventory over the seven parts found:

| Measure | Result |
|---|---:|
| unique conversation tables | 86 |
| conversations present in more than one part | 3 |

No table name or digest is recorded in this gate.

**G2-3: PASS.**

### G2-4 — real cross-part pagination

One overlapping conversation was selected internally without recording its
identity. Its independent raw row count was 1,015. The page size was chosen
from the newest real part so that pagination had to cross a part boundary.

| Measure | Result |
|---|---:|
| independent raw rows | 1,015 |
| rows recovered through public paging | 1,015 |
| pages | 3 |
| first-page limit | 389 |
| duplicate public message ids across pages | 0 |
| duplicate public sequences across pages | 0 |
| every later-page sequence below its cursor | yes |
| exhausted total equals independent raw total | yes |

This verifies that restarted shard-local ids no longer break paging.

**G2-4: PASS.**

### G2-5 — real public message identity

The WAL-aware snapshot contained 532,860 message rows across the seven parts.
Projection used only structural identity evidence.

| Measure | Result |
|---|---:|
| rows checked | 532,860 |
| public message-id collisions | 0 |
| local-only rows using negative fallback ids | 2 |
| fallback deterministic on repeat construction | yes |

Positive source-authored ids and negative structural fallbacks remained
disjoint by sign.

**G2-5: PASS.**

### G2-6 — real public sequence

Across the same 532,860 rows:

| Measure | Result |
|---|---:|
| sequence collisions within a conversation | 0 |
| public sequence order equals `(timestamp, local_id)` order | yes |
| all generated sequences fit signed int64 | yes |

Equal sequence values across different conversations are legal; the provider
uses conversation identity as the scope for collision refusal and retains a
stable mixed-conversation tie-break.

**G2-6: PASS.**

### G2-7 — valid empty parts do not weaken coverage

All three structurally valid empty message parts remained `readable` and
created no inventory gap.

**G2-7: PASS.**

### G2-8 — WAL is material acquisition evidence

A main-file-only decrypt was compared with a private snapshot produced by
replaying active encrypted WAL frames into an encrypted copy before decryption.
No live source was modified.

| Measure | Result |
|---|---:|
| rows absent from main-only message snapshot | 2 |
| main-only latest moment lag | 62 seconds |
| WAL-replayed SQLite integrity check | `ok` |

Therefore a future production acquisition design must be WAL-aware. This fact
is acquisition evidence only; WAL handling is not part of the isolated Reader
provider and was not added by G2.

**G2-8: PASS.**

## 4. Final regression suites

After the R1 sealing correction and before this gate was sealed:

| Suite | Passed |
|---|---:|
| provider | 157 |
| wechatdb | 201 |
| bridge | 179 |
| memory | 348 |
| shadow | 158 |
| Memory layering | 8 |

`git diff --check` was clean.

## 5. Isolation and non-promotion

G2 changes none of the product integration boundaries:

- the DB provider remains isolated and unregistered;
- product core still imports only the generic Reader boundary;
- `selected_source_name()` still defaults to visual;
- the MCP surface is unchanged;
- no key extraction, decryption, process instrumentation, app re-signing, WAL
  replay or filesystem discovery was added to shipped code;
- no real database, key, message content, user identity or account path is
  stored in Git.

## 6. Gate result

**G2 REAL MULTI-PART GATE = MET.**

Under D-032, the scoped promotion evidence state is now:

| Promotion gate | State after G2 |
|---|---|
| P1 — explicit product promotion decision | **UNMET** |
| P2 — standing acquisition decision | **UNMET** |
| P3 — real multi-part verification | **MET under D-032** |
| P4 — complete-container / future-version compatibility | **UNMET** |
| P5 — visual remains production/default and DB remains opt-in/unwired | **UNMET** |

P3 being met does not imply any other gate, does not authorize wiring, and does
not authorize standing acquisition. Visual capture remains the production and
default path.

## 7. Next decision

The next step is not implementation by default. The next gate is **P1: an
explicit product decision about whether the isolated DB provider should be
promoted, and if so, to what role**.

A later product-acquisition design must separately address standing consent,
key storage, WAL-aware snapshots, failure isolation and version compatibility.
