# G1 — synthetic DB reader coverage implementation gate

> **G1 BEING GREEN IS NOT PRODUCTION PROMOTION.**
>
> The promotion gates of spec §15 remain unmet, all of them:
>
> - **P1 — An explicit product decision to promote**, recorded in
>   `Decisions.md`. None exists. An import statement, a green test run and a
>   merged branch are none of them a promotion decision.
> - **P2 — Standing acquisition remains a separate decision.** Not taken.
> - **P3 — D-030 has lapsed** and is not reopened. Real multi-part
>   verification is **not satisfiable today**.
> - **P4 — Complete-container coverage and future-WeChat-version
>   compatibility remain unproven.**
> - **P5 — Visual capture stays the default.** It is the production path.
>
> D-030 is lapsed. Real multi-part verification is not satisfiable today.
> Visual capture remains the production/default path. G1 implies none of the
> above and this document argues for none of them.

## 1. Gate identity

| | |
|---|---|
| Gate | G1 — the full synthetic matrix T-1…T-18 is green, each verified to fail on revert (spec §15) |
| Branch | `feature/hermes-validation-isolation` (unmerged) |
| HEAD the gate was performed against | `f07670a` |
| Date | 2026-09-19 |
| Governing decision | D-031, amendments 3–9 |
| Spec / plan | `docs/superpowers/specs/2026-09-18-db-reader-coverage-provider-design.md`, `docs/superpowers/plans/2026-09-18-db-reader-coverage-provider.md` (P18) |

## 2. Scope

Synthetic fixtures only. The candidate provider remains unwired. No real
database was used. No acquisition. No key, decryption or process access. Every
part a provider test reads is a temporary SQLite file built in code under
pytest's `tmp_path`, with invented identities.

`PYTEST` below is the canonical runner:
`uv run --python 3.12 --with pytest --with pytest-asyncio --with "mcp[cli]" --with zstandard python -m pytest`.
Mutation runs added `-p no:cacheprovider`, `PYTHONDONTWRITEBYTECODE=1` and a
fresh `PYTHONPYCACHEPREFIX` per run (see §4, note 1).

## 3. T-1…T-18 map

Paths: `WP` = `wechatdb/tests/provider/`, `BT` = `bridge/tests/`, `MT` = `memory/tests/`.

| T | Behaviour | Task (commit) | File | Test |
|---|---|---|---|---|
| T-1 | all parts readable, full window ⟹ complete | P17a (`c4b80b5`) + P16 (`22e1976`) | `WP/test_provider_reads.py` | `test_all_parts_readable_over_the_full_window` |
| T-2 | unknown part changes the claim, not the content | P17b (`ac726e5`) + P16 | `WP/test_provider_degradation.py`; `WP/test_result.py` | `test_an_unknown_intersecting_part_changes_the_claim_not_the_content`; `test_any_inventory_gap_makes_the_read_partial` |
| T-3 | unavailable part (will not open / unrecognised schema) | P17b + P16 | `WP/test_provider_degradation.py` | `test_an_unavailable_part_still_returns_the_readable_parts` |
| T-4 | read spanning parts: (a) accountable ⟹ complete; (b) weakest complete point caps | P17a + P16 | `WP/test_provider_reads.py`; `WP/test_result.py` | `test_a_conversation_spanning_parts_merges_in_order`, `test_a_conversation_spanning_parts_caps_at_the_weakest_complete_point`; `test_one_capped_contribution_caps_the_whole_read` |
| T-5 | safe limited traversal stays caller-limited | P17b + P16 (+ P14 `d42ef13`) | `WP/test_provider_degradation.py` | `test_a_safe_early_stop_preserves_a_correct_caller_limited_answer` |
| T-6 | unsafe early stop, two variants | P17b + P16 (+ P14) | `WP/test_provider_degradation.py` | `test_an_unsafe_early_stop_is_partial` |
| T-7 | source newer than the read: inside / outside / absent | P17b + P16 | `WP/test_provider_degradation.py` | `test_a_source_newer_than_the_read_downgrades_freshness` |
| T-8 | identity ambiguity refused, never guessed | P15 (`6b37224`) | `WP/test_identity.py` | `test_two_conflicting_same_kind_names_resolve_to_no_name`, `test_an_ambiguous_stronger_kind_does_not_fall_back_to_a_weaker_name` |
| T-9 | zero messages, every part readable ⟹ trustworthy empty | P17a | `WP/test_provider_reads.py` | `test_zero_messages_with_every_part_readable_is_a_trustworthy_empty` |
| T-10 | zero messages, a part unavailable ⟹ not trustworthy (pair of T-9, shared fixture) | P17b | `WP/test_provider_degradation.py` | `test_zero_messages_with_an_unavailable_part_is_not_trustworthy` |
| T-11 | source-internal truncation below the caller's limit | P9 (`c8ee167`) + P17b (+ P16) | `BT/test_reader_boundary.py`; `WP/test_provider_degradation.py`; `WP/test_result.py` | `test_the_bounded_conversation_sweep_reports_its_own_limit`, `test_a_short_answer_to_a_large_request_is_not_complete`; `test_the_provider_truncating_internally_is_never_complete`; `test_a_truncated_contribution_is_source_limit_even_below_the_caller_limit` |
| T-12 | visual store is conservative | P10 (`7bb6cb3`) | `BT/test_reader_boundary.py` | `test_a_filled_limit_on_the_visual_store_is_partial` (with `test_the_visual_store_never_reports_complete_from_item_count_alone`) |
| T-13 | Rion pagination preserved | P9 | `BT/test_reader_boundary.py` | `test_upstream_has_more_is_preserved_as_partial_coverage`, `test_next_offset_never_appears_in_coverage` |
| T-14 | Memory records source coverage, never infers from length | P11 (`7f27be1`) | `MT/test_memory_ingest.py` | `test_a_short_answer_is_not_recorded_complete_when_the_source_says_partial`, `test_the_length_inference_is_gone_from_the_ingestor` |
| T-15 | boundary is technology-neutral | P3 (`dd952c7`) + P0 guard (`4a28761`) | `BT/test_reader_boundary.py` | `test_the_protocol_depends_on_no_reader_technology`, `test_the_boundary_module_never_gains_a_digest_dependency`, `test_only_one_implementation_of_conversation_identity_exists` |
| T-16 | import direction; leaf parser isolation | P12 (`6f59f53`) | `BT/test_reader_boundary.py`; `WP/test_isolation.py` | `test_no_product_module_imports_the_candidate_schema_provider`; `test_importing_the_leaf_parser_does_not_import_the_provider` |
| T-17 | construction invariants enforced | P5 (`0283519`) + P6 (`f05ad2a`) | `BT/test_read_coverage.py` | `test_a_complete_read_can_never_be_truncated`, `test_a_result_whose_count_disagrees_with_its_items_is_refused` |
| T-18 | closed reason vocabulary | P4 (`b631247`) + P17b (sealed `f07670a`) | `BT/test_reader_boundary.py`; `WP/test_provider_degradation.py` | `test_the_reason_vocabulary_is_closed`; `test_every_reason_token_this_provider_emits_is_in_the_closed_set` (passes standalone) |

No T-number is unmapped.

## 4. Revert-verification evidence

Every row was observed in this session. Procedure per mutation: clean tree →
one temporary mutation → named tests run → mutated file restored with
`git restore --source=HEAD -- <file>` → same tests rerun green → `git status`
empty. No mutation was carried into the next. Commands are run from the
package directory named, as `PYTEST -q <node ids>`.

| # | Mutation (file) | Protects | Failing test(s) | Observed failure | Restored |
|---|---|---|---|---|---|
| A1 | collapse always authors complete (`wechatdb/provider/result.py`) | T-2, T-3, T-6, T-10, T-11 | the T-2, T-3, T-6, T-10, T-11 provider tests and P16 `test_a_truncated_contribution_is_source_limit_even_below_the_caller_limit` — 6 of 6 red | `('observed_complete', …) == ('observed_partial', 'partial_inventory')`; `'observed_complete' == 'observed_partial'` (will not open); `… == (…, 'unsafe_early_stop')` | 6 passed |
| A2 | Rion adapter ignores `has_more` and its sweep bound (`bridge/rion_reader_adapter.py`) | T-13, T-11 (P9) | `test_upstream_has_more_is_preserved_as_partial_coverage`, `test_the_bounded_conversation_sweep_reports_its_own_limit`, `test_a_short_answer_to_a_large_request_is_not_complete` — 3 of 3 | `'observed_complete' == 'observed_partial'` ×2; `'observed_complete' != 'observed_complete'` | 3 passed |
| A3 | visual store treats a filled limit as ordinary (`bridge/store_access.py`) | T-12 | `test_a_filled_limit_on_the_visual_store_is_partial` | `'timestamp_mismatch' == 'caller_limit'` | passed |
| B | `classify_stop` always `STOP_SAFE` (`routing.py`) | T-6 | `test_an_unsafe_early_stop_is_partial` | variant "unvisited maximum at or after the boundary": `(…, 'caller_limit') == (…, 'unsafe_early_stop')` | 1 passed |
| C | `classify_stop` always `STOP_UNSAFE` (`routing.py`) | T-5 | `test_a_safe_early_stop_preserves_a_correct_caller_limited_answer` | `{'message_1.db': 2} != {'message_1.db': 1}` — the skipped part was read | 1 passed |
| D | trim to the limit before collapse (`provider.py`) | T-5, T-4a, sentinel ordering | T-5; `test_a_conversation_spanning_parts_merges_in_order`; `test_the_sentinel_reaches_collapse_before_the_public_trim` — 3 red | `ValueError: a safe stop requires measured caller truncation`; `'full_window_observed' == 'caller_limit'`; `assert 2 > 2` | passed |
| E | `item_count` counts the sentinel (`result.py`) | sentinel / caller-limit path, T-5, T-17 inv. 11 | sentinel test, T-5, P16 `test_a_sentinel_measures_caller_truncation_but_is_not_counted_publicly` — 3 of 3 | `ValueError: coverage item count disagrees with the items` ×2; `('caller_limit', True, 3) == ('caller_limit', True, 2)` | 3 passed |
| F | source truncation reported as `caller_limit` (`result.py`) | T-11, T-4b | provider T-11, T-4b, P16 T-11 — 3 of 3 | `(…, 'caller_limit') == (…, 'source_limit')` | 3 passed |
| G | `probe()` drops unknown/unavailable parts (`discovery.py`) | T-2, T-3, T-10 | the three tests — 3 of 3 | `('observed_complete', …) == ('observed_partial', 'partial_inventory')`; T-10: `(…, 'empty_window') == (…, 'partial_inventory')` | 3 passed |
| H | `max(complete_through)` (`result.py`) | T-4b | provider T-4b; P16 `test_one_capped_contribution_caps_the_whole_read` | `assert 400 == 200` | 2 passed |
| I | resolver picks a name under ambiguity (`identity.py`) | T-8 | both T-8 tests | `'wxid_fixture_x' not in {'wxid_fixture_x': 'Fixture Remark One'}`; `… {'wxid_fixture_x': 'Room Alice'}` — the ambiguous name was no longer refused | 2 passed |
| J | `len(messages) < message_limit` restored (`memory/memory_ingest.py`) | T-14 | both T-14 tests | `'observed_complete' == 'observed_partial'`; AST guard: `['len(messages) < message_limit'] == []` | 2 passed |
| K | boundary `import json` (`bridge/message_source.py`) | T-15 | `test_the_protocol_depends_on_no_reader_technology` | extra item in import set: `'json'` | passed |
| L | `import wechatdb` in `bridge/store_access.py` | T-16 | `test_no_product_module_imports_the_candidate_schema_provider` | `['bridge/store_access.py'] == []` | passed |
| M | `from . import provider` in `wechatdb/__init__.py` | T-16 / P12 leaf isolation | `test_importing_the_leaf_parser_does_not_import_the_provider` | `'True True' == 'False False'` | passed |
| N | second `conversation_identifier` defined in `provider/result.py` | P0 uniqueness (T-15) | `test_only_one_implementation_of_conversation_identity_exists`; P16 `test_the_provider_derives_identity_from_the_generic_owner` | `['wechatdb/provider/result.py'] == []`; identifiers disagree | passed |
| O | boundary `import hashlib` | P0 digest guard, T-15 | `test_the_boundary_module_never_gains_a_digest_dependency`, `test_the_protocol_depends_on_no_reader_technology` | `'hashlib' not in {…, 'hashlib', …}` | 2 passed |
| P | complete ⟹ not-truncated invariant bypassed (`message_source.py`) | T-17 | `test_a_complete_read_can_never_be_truncated` | **first run: NOT RED — decorative, see §4.1.** After strengthening: regex mismatch, got `truncation requires a cut short reason`, wanted `a complete read cannot be truncated` | passed |
| P2 | `ReadResult` count invariant bypassed | T-17 | `test_a_result_whose_count_disagrees_with_its_items_is_refused` | `Failed: DID NOT RAISE ValueError` | passed |
| Q1 | provider emits a thirteenth reason `"inventory_hole"` (`result.py`) | T-18 | provider T-18 and T-2 | `ValueError: coverage reason is not in the closed vocabulary` — the boundary's closure invariant refusing it, not a syntax error | 2 passed |
| Q2 | boundary vocabulary gains a thirteenth token | T-18 (P4) | `test_the_reason_vocabulary_is_closed` | `assert 13 == 12` | passed |
| R | provider reads only the first planned part (`provider.py`) | T-1, T-4a | both tests | `[3, 6] == [1, 2, 3, 4, 5, 6]`; `[200, 400, 600] == [100, …, 600]` | 2 passed |
| S | an empty complete read is called full (`result.py`) | T-9 | T-9 | `(…, 'full_window_observed') == (…, 'empty_window')` | passed |
| T | provider withholds `source_newest` (`provider.py`) | T-7 | T-7 | `('observed_complete', …, UNKNOWN) == ('observed_partial', 'timestamp_mismatch', POTENTIALLY_STALE)` | passed |

Every T-number has at least one observed RED: T-1 R · T-2 A1,G,Q1 · T-3 A1,G ·
T-4 R,H,F,D · T-5 C,D,E · T-6 A1,B · T-7 T · T-8 I · T-9 S · T-10 A1,G ·
T-11 A1,A2,F · T-12 A3 · T-13 A2 · T-14 J · T-15 K,N,O · T-16 L,M · T-17 P,P2,E ·
T-18 Q1,Q2.

Notes, recorded rather than smoothed over:

1. **A harness artefact was found and removed before any evidence was kept.**
   The first pass reported F as not red and H as red *after* restore. Both were
   stale bytecode: a same-size edit restored within one second leaves a `.pyc`
   whose size-and-mtime check still matches. All `__pycache__` was deleted and
   every run thereafter used a fresh `PYTHONPYCACHEPREFIX`; the whole set was
   then rerun, and the table above is that second pass.
2. Tests run under a mutation that were **not** expected to detect it and did
   not: under A3, `test_the_visual_store_never_reports_complete_from_item_count_alone`
   (it pins the short-answer path, not the filled limit); under D, provider
   T-11 (its three records are below the limit, so trimming changes nothing —
   the plan names no T-11 obligation for this mutation); under Q2,
   `test_the_reason_status_mapping_is_total_in_both_directions` (it pins
   `REASON_STATUSES`, not `COVERAGE_REASONS`). None is counted as evidence.

### 4.1 Decorative test found and strengthened

`bridge/tests/test_read_coverage.py::test_a_complete_read_can_never_be_truncated`
asserted a bare `ValueError`. With invariant 3 bypassed, invariant 6
("truncation requires a cut short reason") raises a `ValueError` for the same
input, so the test stayed green — its docstring's claim that "nothing but
invariant 3 can be what fires" was untrue. The test now matches invariant 3's
own message, `^a complete read cannot be truncated$`. Sequence observed:
strengthened test green on correct production → mutation P reapplied → red →
restored → green. This is the only test change in P18; no production file
changed.

## 5. Final green suites (at the gate commit)

| Suite | Result |
|---|---|
| bridge | 179 passed |
| memory | 348 passed |
| wechatdb | 183 passed |
| shadow | 158 passed |
| provider (`wechatdb/tests/provider`) | 141 passed |
| Memory layering (`memory/tests/test_layering.py`) | 8 passed |
| `git diff --check` | clean |

The pre-mutation baseline was identical. The four P17 sealing regressions —
one locator snapshot per read, the newest-window limited read, ambiguity events
not deduplicated by identifier, and T-18 standalone — pass, run by node id.

## 6. Isolation status

- `selected_source_name()` still returns `visual` by default
  (`test_the_default_source_is_the_visual_store`, green).
- No product module (`bridge/`, `memory/`, `shadow/`, `ai/`, `core/`, `app.py`,
  `mcp_server.py`) imports `wechatdb` or its provider: the guard is green and a
  text search outside test directories finds nothing.
- The provider is not registered: no `SOURCE_NAMES`, environment variable,
  `build_database_source()` or launcher change.
- The MCP surface is unchanged: exactly four tools
  (`test_the_tool_surface_is_exactly_the_four_tools`, green).
- `wechatdb` top level remains leaf-only; importing it does not load the
  provider (mutation M).
- No acquisition capability exists: P12's acquisition guard and P13's
  path-search guard are green over every provider module.
- Production files are byte-identical to `f07670a`
  (`git diff --quiet f07670a -- bridge/message_source.py bridge/store_access.py
  bridge/rion_reader_adapter.py memory/memory_ingest.py wechatdb/__init__.py
  wechatdb/provider` exits 0).

## 7. Conclusion

Every required mutation was observed red against a named test, the one
decorative assertion found was strengthened and re-verified, every mutation was
restored, and the final suites are green.

**G1 = MET.**

G1 is the implementation gate only. See the statement at the top of this
document: P1–P5 remain unmet, and the next step is an explicit decision about
whether to pursue them at all — not wiring.
