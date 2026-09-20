# DB Reader Acquisition Fast Lane — Real End-to-End Gate

> This gate records structural evidence only. It contains no database keys,
> usernames, contact names, message content, table digests, account identifiers,
> or live source paths.

## 1. Gate identity

| | |
|---|---|
| Scope | current-version stored-key database acquisition Fast Lane |
| Synthetic implementation | `1acb13f` — `feat(v2): prepare database reader from stable snapshots` |
| Real-evidence sealing fix | `3e95692` — `fix(v2): honor active wal-index frame boundary` |
| Product decisions | P1/P2/P3 MET; D-034 + D-035 |
| Current WeChat evidence | 4.1.15 |
| Runtime wiring | none |
| Bootstrap | not implemented |
| OCR work | none |

The real gate used existing operator-owned private access material only as an
in-memory test input. No key was added to the repository or printed. The macOS
Keychain adapter itself had already passed a separate bounded live gate using
random disposable material at `afdf1ca`.

## 2. Independent synthetic regression

Before the real gate, independent validation passed:

| Suite | Result |
|---|---:|
| acquisition before sealing | 100 passed |
| full repository before sealing | 1002 passed |
| acquisition after sealing | 106 passed |
| full repository after sealing | 1008 passed |
| `compileall acquisition` | exit 0 |
| `git diff --check` | clean |

## 3. Real WAL-index finding

The first real Fast Lane attempt failed closed as:

`version_unverified`

The failure was traced to a real SQLite WAL lifecycle property rather than to
key storage or decryption: physical WAL files may retain old frame bytes beyond
the active wal-index generation.

Structural examples from the bounded source set:

| Role | Physical WAL frames | active SHM `mxFrame` |
|---|---:|---:|
| one active message part | 1018 | 12 |
| another message part | 2349 | 0 |
| another message part | 1018 | 0 |
| session source | 1018 | 20 |

A physical frame count is therefore not an active-frame boundary.

The real SHM headers independently verified:

- both 48-byte wal-index header copies were identical;
- the wal-index header checksum was valid;
- active-header raw salts matched the WAL header;
- SHM `aFrameCksum` matched the checksum at `mxFrame`;
- SHM `nPage` matched the committed database size at `mxFrame`.

## 4. Sealing correction

`3e95692` makes SHM provide the active-frame upper bound while leaving WAL
itself responsible for proving integrity and commit semantics.

For a supplied SHM, replay now requires:

- identical wal-index header copies;
- valid wal-index header checksum;
- supported wal-index version/page size;
- active-generation salt correspondence;
- WAL frame checksum chain through `mxFrame`;
- `mxFrame` itself to be a complete commit;
- SHM `aFrameCksum` to equal the checksum of that frame;
- SHM `nPage` to equal that frame's committed database size.

`mxFrame == 0` means that physical WAL tail bytes are inactive residue and are
not replayed.

The WAL is never trusted beyond the SHM active boundary and SHM is never used to
invent a commit the WAL cannot prove.

## 5. Real end-to-end result

After the sealing correction, the same bounded current-version gate returned:

| Check | Result |
|---|---:|
| Acquisition state | `ready` |
| prepared message handles | 7 |
| conversation identity handle | present |
| display identity handle | present |
| IdentityCatalog session mappings | 8,638 |
| IdentityCatalog display candidates | 8,888 |
| provider conversations | 86 |
| provider conversation coverage | `observed_complete / full_window_observed` |
| parser fallback conversation titles | 0 |
| recent messages sampled | 1,000 |
| recent coverage | `observed_partial / caller_limit` |
| public ids unique in sample | yes |
| `(conversation_id, sequence)` unique in sample | yes |
| provider readable parts | 7 |
| provider unknown / unavailable parts | 0 / 0 |
| unresolved identity diagnostics | 0 |
| lease workspace residues after exit | **0** |

Therefore the normal stored-key database path is now proven end to end for the
bounded current-version source:

```text
stored key material
    -> stable private encrypted snapshot
    -> SHM-bounded committed WAL replay
    -> authenticated decrypt workspace
    -> PreparedSource
    -> IdentityCatalog
    -> ShardedMessageProvider
```

## 6. What this gate does not prove

This gate does not implement or prove:

- first-run/key-refresh bootstrap;
- Frida/process-inspection helper boundaries;
- source/account auto-discovery;
- unknown future WeChat versions;
- product source routing or provider registration;
- OCR/visual implementation changes.

Visual remains the current runtime default and the database provider remains
unwired.

## 7. Gate result

**ACQUISITION FAST LANE REAL END-TO-END GATE = MET.**

P1/P2/P3 remain MET. P4 and P5 remain UNMET.

The next accelerated work is:

1. define and seal the P4 compatibility envelope for the currently proven
   format, with unknown/changed formats failing closed as `version_unverified`;
2. implement P5 opt-in runtime wiring: Database Mode uses the DB Reader when
   acquisition is READY and falls back to the existing visual Reader otherwise;
3. keep bootstrap/key refresh as a separate explicit helper path for first-run
   and invalidated-key cases.
