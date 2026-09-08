# H4.1 focused validation — evidence (synthetic only)

**Synthetic only.** No real WeChat data, container, database or access
material. Every store was built from the H3 scenario fixtures. The official
Hermes install and the `wechatshadow` profile were not written to.

Scope is deliberately narrow: this run re-establishes what the SKILL.md v1.2.0
change could affect. The B/C source-composition work and the exact-8 boundary
were not re-derived as new evidence — the change touches digest *text*, not
tool resolution — though both ran incidentally and are recorded below.

## Environment

| Item | Value |
|---|---|
| Runtime | upstream `main` `b2aa855b6` + `#89550` `c75e9cd63` + `#88865` `4e844b3fe`, composed at `95c35cd7e` |
| Skill | `wechat-digest` **v1.2.0** |
| Invocation | `-s wechat-digest` (CLI runner); SKILL.md bytes prepended (Desktop turn) |
| Platform | `HERMES_DESKTOP=1`, no Electron build |
| Isolation | disposable `HOME` + `HERMES_HOME` per run, enforced pre-import by `hermes_isolation` |

## Static gates

| Suite | Result |
|---|---|
| `evaluation/` (skill rules, incl. two new H4.1 assertions) | **52 passed** |
| `shadow/tests/` (harness, isolation guard, runners, proxy) | **158 passed** |

## Behavioural gates — every digest, both rules

| Digest | status | coverage line is literal last line | internal names |
|---|---|---|---|
| Gate A — unanswered question | 0 | ✅ | none |
| Gate B — already answered | 0 | ✅ | none |
| Gate C — deadline request | 0 | ✅ | none |
| Gate J — instruction-shaped text | 0 | ✅ | none |
| Canary | 0 | ✅ | none |
| **Desktop turn** | ok | ✅ | none |

Verified twice: by the harness during the run, and independently afterwards by
re-parsing the raw log and the stored Desktop reply against the literal
coverage line and the `INTERNAL_NAMES` list.

**The Desktop turn is the one that matters.** It is the surface that previously
appended `（提示：当前仅采集到 1 个会话、2 条消息，覆盖范围非常有限。）` after
the coverage line. Its reply is now 128 bytes and ends exactly on the coverage
line:

```
微信摘要

🔴 需要处理
- Chat A（发送者：Sender One，14:30）：…未见到本人回复。

基于 WeChat Companion 已采集到的消息生成，可能不包含未被采集的聊天。
```

## Canary — persistence

Token present only in the disposable `state.db` before purge; **zero hits after
purge**; sessions purged to zero; zero request dumps. `digest_contained_canary`
is recorded, never asserted — the canary is a persistence audit, and a digest
summarising a message that contains a random string is correct behaviour.

## Boundary (incidental, not re-derived)

Both wire assertions held throughout, as they must for any run to proceed:
Desktop and CLI surfaces, every tools-bearing request carrying exactly the
eight expected `mcp__wechat_companion__*` schemas, zero extra, zero missing,
zero violations.

## Residue and containment

| Check | Result |
|---|---|
| Request dumps (all 16 disposable profiles) | 0 |
| Session databases | 0 |
| Updater backups | 0 |
| Credential residue | none |
| Credential hand-off file | consumed and deleted before the first provider call |
| Live validation processes / open proxy ports | 0 / 0 |
| `~/.hermes/SOUL.md` | 514 bytes, `29993087…`, mtime 2026-08-24 22:24:02 |
| Anything written under `~/.hermes` | none |
| Official install | `a08dfab3`, clean tree, 1 reflog entry |
| `wechatshadow` profile | untouched |

## Verdict

**PASS.** Both H4.1 rules hold on every digest the supported invocation
produces, on the surface that previously broke one of them. The invocation
contract is scoped and documented, and no project claim asserts that a seeded
`-q` performs server-side slash expansion.

Still open, unaffected by this change: the third H4.1 item, a regression test
for the stray `skill_manage` deviation.
