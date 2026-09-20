"""ProviderResult: the single provider -> generic projection point.

Two projections and nothing else: a parser record into a NormalizedMessage,
and provider-local read evidence into exactly one ReadCoverage. Everything
here is built directly from synthetic values; no database is opened, no shard
is routed, no name is resolved, and no ReadResult is constructed by production
code -- a test constructs one only to prove the coverage fits the public tuple.
"""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

import pytest

import message_source as ms
from conversation_identity import conversation_identifier
from wechatdb.parser import MessageRecord
from wechatdb.provider import result as result_module
from wechatdb.provider.result import Contribution, ProviderDiagnostics, ProviderResult
from wechatdb.provider.routing import STOP_EXHAUSTED, STOP_SAFE, STOP_UNSAFE

SOURCE = Path(result_module.__file__).resolve()
ROOT = SOURCE.parents[2]
SESSION = "fixture_session_alpha"


def record(local_id, timestamp=1_756_000_000, *, session=SESSION, sender_name="Fixture Sender",
           content="fixture text", kind="text"):
    return MessageRecord(session_id=session, local_id=local_id, timestamp=timestamp,
                         sender_id="wxid_fixture_sender", sender_name=sender_name,
                         message_type=kind, content=content, media_id=None, reply_to=None)


def contribution(count, *, observed, complete="same", truncated=False, first_id=1):
    return Contribution(
        records=tuple(record(first_id + i) for i in range(count)),
        observed_through=observed,
        complete_through=observed if complete == "same" else complete,
        truncated=truncated,
    )


def collapse(contributions, **overrides):
    arguments = dict(requested_start=None, requested_end=None, caller_limit=50,
                     stop=STOP_EXHAUSTED, inventory_gap=False, source_newest=None)
    arguments.update(overrides)
    return ProviderResult.collapse(contributions, **arguments)


# --- Contribution -----------------------------------------------------------

def test_contributions_are_sealed_at_construction():
    Contribution(records=(), observed_through=None, complete_through=None, truncated=False)
    Contribution(records=(record(1),), observed_through=10, complete_through=10, truncated=True)
    Contribution(records=(record(1),), observed_through=10, complete_through=5, truncated=True)
    secret = "fixture secret content"
    for bad in (
        dict(records=[record(1, content=secret)], observed_through=1, complete_through=1, truncated=False),
        dict(records=(record(1, content=secret),), observed_through=1, complete_through=1, truncated=1),
        dict(records=(record(1, content=secret),), observed_through=1, complete_through=1, truncated=None),
        dict(records=(), observed_through=None, complete_through=5, truncated=False),
        dict(records=(), observed_through=5, complete_through=6, truncated=False),
    ):
        with pytest.raises(ValueError) as refusal:
            Contribution(**bad)
        assert secret not in str(refusal.value)
    assert [f.name for f in dataclasses.fields(Contribution)] == [
        "records", "observed_through", "complete_through", "truncated"]


# --- ProviderDiagnostics ------------------------------------------------------

def test_diagnostics_carry_counts_only():
    fields = ["readable", "unknown", "unavailable", "unresolved_identities"]
    assert [f.name for f in dataclasses.fields(ProviderDiagnostics)] == fields
    ok = dict(readable=3, unknown=1, unavailable=2, unresolved_identities=0)
    assert ProviderDiagnostics(**ok).unavailable == 2
    for name in fields:
        for bad in (True, False, -1, 1.0, "1", None):
            with pytest.raises(ValueError):
                ProviderDiagnostics(**{**ok, name: bad})


def test_diagnostics_count_ambiguity_events_not_unique_identifiers():
    """P15's count is per resolve() call; a read sums them.

    One identifier ambiguous in two rooms is two events. Nothing in the type
    could deduplicate them, because it holds no identifier at all -- which is
    what keeps the count-only privacy contract.
    """
    per_call = [1, 1, 0]           # the same identifier, ambiguous in two rooms
    diagnostics = ProviderDiagnostics(readable=2, unknown=0, unavailable=0,
                                      unresolved_identities=sum(per_call))
    assert diagnostics.unresolved_identities == 2
    assert all(isinstance(getattr(diagnostics, f.name), int)
               for f in dataclasses.fields(ProviderDiagnostics))
    assert "events" in (ProviderDiagnostics.__doc__ or "")


# --- the message projection ---------------------------------------------------

def test_message_projection_uses_unknown_ownership_without_guessing_self():
    projected = ProviderResult.message(record(
        7, 1_756_000_123, sender_name="Me (the account owner)", content="fixture body",
        kind="quote"))
    assert isinstance(projected, ms.NormalizedMessage)
    assert dataclasses.asdict(projected) == {
        "id": 7,
        "conversation_id": conversation_identifier(SESSION),
        "sequence": 7,
        "sender": "Me (the account owner)",
        "ownership": "unknown",
        "visible_time": None,
        "text": "fixture body",
        "kind": "quote",
        "confidence": 1.0,
        "first_observed_at": 1_756_000_123,
        "source": ms.SOURCE_DATABASE,
    }
    # Whole seconds are valid evidence in the float contract; nothing cast them.
    assert type(projected.first_observed_at) is int
    # No string shape makes a message "own".
    for name in ("me", "self", "wxid_fixture_self", None):
        assert ProviderResult.message(record(1, sender_name=name)).ownership == "unknown"


def test_the_provider_derives_identity_from_the_generic_owner():
    first = ProviderResult.message(record(1))
    second = ProviderResult.message(record(999))
    assert first.conversation_id == second.conversation_id == conversation_identifier(SESSION)
    assert (first.id, second.id) == (1, 999)             # local_id never feeds the conversation

    other = ProviderResult.message(record(1, session="fixture_session_beta"))
    assert other.conversation_id == conversation_identifier("fixture_session_beta")
    assert other.conversation_id != first.conversation_id

    fallback = ProviderResult.message(record(1, session="msg_" + "0123456789abcdef" * 2))
    assert fallback.conversation_id == conversation_identifier("msg_" + "0123456789abcdef" * 2)

    with pytest.raises(TypeError):
        ProviderResult.message(record(1), conversation_id=123)   # no caller override exists

    # The P0 architecture guard, unweakened.
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imported = {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    assert ("conversation_identity", "conversation_identifier") in imported
    assert not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == "conversation_identifier" for n in ast.walk(tree))
    assert not any(isinstance(n, ast.Attribute) and n.attr == "blake2b" for n in ast.walk(tree))
    for path in (ROOT / "wechatdb").rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            assert not any(n.split(".")[0] == "rion_reader_adapter" for n in names), path.name


# --- collapse: inputs -----------------------------------------------------------

def test_collapse_refuses_malformed_provider_inputs():
    one = [contribution(1, observed=10)]
    for bad in (0, -1, True, 2.0, "2", None):
        with pytest.raises(ValueError):
            collapse(one, caller_limit=bad)
    for bad in ("finished", "", None, "SAFE"):
        with pytest.raises(ValueError):
            collapse(one, stop=bad)
    for bad in (0, 1, None, "yes"):
        with pytest.raises(ValueError):
            collapse(one, inventory_gap=bad)
    with pytest.raises(ValueError):                       # the generic constructor's own rule
        collapse(one, requested_start=200.5, requested_end=100.25)


# --- collapse: complete -----------------------------------------------------------

def test_every_contribution_accountable_yields_a_complete_read():
    coverage = collapse([contribution(2, observed=400), contribution(3, observed=350, first_id=10)])
    assert (coverage.status, coverage.reason) == (ms.COVERAGE_COMPLETE, ms.REASON_FULL_WINDOW_OBSERVED)
    assert coverage.truncated is False
    assert coverage.item_count == 5
    assert coverage.observed_through == 400
    assert coverage.complete_through == coverage.observed_through


def test_a_complete_read_never_has_differing_through_points():
    """Differing provider-local complete points, none of them a cut."""
    coverage = collapse([contribution(1, observed=400, complete=400),
                         contribution(1, observed=350, complete=200, first_id=5)])
    assert coverage.status == ms.COVERAGE_COMPLETE
    assert coverage.complete_through == coverage.observed_through == 400


def test_a_complete_empty_uses_empty_window_and_zero_public_count():
    for contributions in ([], [contribution(0, observed=None)], [contribution(0, observed=500)]):
        coverage = collapse(contributions)
        assert (coverage.status, coverage.reason) == (ms.COVERAGE_COMPLETE, ms.REASON_EMPTY_WINDOW)
        assert coverage.item_count == 0 and coverage.truncated is False
        assert coverage.complete_through == coverage.observed_through


# --- collapse: the partial reasons ----------------------------------------------

def test_one_capped_contribution_caps_the_whole_read():
    """T-4b: the weakest complete point caps the aggregate; the maximum is still reported."""
    coverage = collapse([contribution(1, observed=400, complete=400),
                         contribution(1, observed=350, complete=200, truncated=True, first_id=5)])
    assert (coverage.status, coverage.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_SOURCE_LIMIT)
    assert coverage.truncated is True
    assert (coverage.observed_through, coverage.complete_through) == (400, 200)


def test_a_truncated_contribution_is_source_limit_even_below_the_caller_limit():
    """T-11: three records for a limit of two hundred, internally cut."""
    coverage = collapse([contribution(3, observed=100, truncated=True)], caller_limit=200)
    assert (coverage.status, coverage.reason, coverage.truncated) == (
        ms.COVERAGE_PARTIAL, ms.REASON_SOURCE_LIMIT, True)
    assert coverage.item_count == 3
    assert coverage.status != ms.COVERAGE_COMPLETE


def test_any_inventory_gap_makes_the_read_partial():
    coverage = collapse([contribution(2, observed=400)], inventory_gap=True)
    assert (coverage.status, coverage.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_PARTIAL_INVENTORY)
    assert coverage.truncated is False
    assert coverage.observed_through == 400
    assert coverage.complete_through is None
    empty = collapse([], inventory_gap=True)               # T-10: zero items, not trustworthy
    assert (empty.status, empty.reason, empty.item_count) == (
        ms.COVERAGE_PARTIAL, ms.REASON_PARTIAL_INVENTORY, 0)


def test_an_unsafe_stop_states_no_observed_point():
    coverage = collapse([contribution(3, observed=400, truncated=True)], caller_limit=2,
                        stop=STOP_UNSAFE, inventory_gap=True, source_newest=999.0)
    assert (coverage.status, coverage.reason, coverage.truncated) == (
        ms.COVERAGE_PARTIAL, ms.REASON_UNSAFE_EARLY_STOP, True)
    assert coverage.observed_through is None and coverage.complete_through is None
    assert coverage.freshness is ms.ReadFreshness.UNKNOWN
    assert coverage.item_count == 2


def test_a_safe_limited_stop_remains_caller_limited_not_unsafe():
    """T-5: the early stop was safe for the limited answer, and says nothing more."""
    coverage = collapse([contribution(3, observed=400)], caller_limit=2, stop=STOP_SAFE)
    assert (coverage.status, coverage.reason, coverage.truncated) == (
        ms.COVERAGE_PARTIAL, ms.REASON_CALLER_LIMIT, True)
    assert coverage.observed_through == 400
    assert coverage.reason not in (ms.REASON_UNSAFE_EARLY_STOP, ms.REASON_WINDOW_BOUND)
    assert coverage.status != ms.COVERAGE_COMPLETE


def test_a_safe_stop_without_a_measured_sentinel_is_refused():
    for count in (10, 9, 0):
        with pytest.raises(ValueError) as refusal:
            collapse([contribution(count, observed=400)], caller_limit=10, stop=STOP_SAFE)
        assert str(refusal.value) == "a safe stop requires measured caller truncation"
    lawful = collapse([contribution(11, observed=400)], caller_limit=10, stop=STOP_SAFE)
    assert (lawful.status, lawful.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_CALLER_LIMIT)


def test_exhausting_every_part_never_hides_a_caller_cut():
    coverage = collapse([contribution(2, observed=400), contribution(1, observed=300, first_id=9)],
                        caller_limit=2, stop=STOP_EXHAUSTED)
    assert (coverage.status, coverage.reason, coverage.truncated) == (
        ms.COVERAGE_PARTIAL, ms.REASON_CALLER_LIMIT, True)


def test_a_count_equal_to_the_limit_is_not_a_cut():
    coverage = collapse([contribution(2, observed=400)], caller_limit=2)
    assert (coverage.status, coverage.truncated, coverage.item_count) == (
        ms.COVERAGE_COMPLETE, False, 2)


def test_a_sentinel_measures_caller_truncation_but_is_not_counted_publicly():
    records = (record(1), record(2), record(3))
    coverage = collapse([Contribution(records=records, observed_through=400,
                                      complete_through=400, truncated=False)], caller_limit=2)
    assert (coverage.reason, coverage.truncated, coverage.item_count) == (
        ms.REASON_CALLER_LIMIT, True, 2)
    public = tuple(ProviderResult.message(r) for r in records[:2])
    built = ms.ReadResult(items=public, coverage=coverage)     # constructs, with no repair
    assert len(built.items) == built.coverage.item_count == 2


def test_candidates_are_counted_as_given_with_no_deduplication():
    same = record(1)
    coverage = collapse([Contribution(records=(same, same), observed_through=10,
                                      complete_through=10, truncated=False)], caller_limit=1)
    assert (coverage.reason, coverage.item_count) == (ms.REASON_CALLER_LIMIT, 1)


# --- collapse: precedence ---------------------------------------------------------

def test_reason_precedence_is_fixed_under_mixed_evidence():
    cut = [contribution(3, observed=400, truncated=True)]
    assert collapse(cut, stop=STOP_UNSAFE).reason == ms.REASON_UNSAFE_EARLY_STOP          # unsafe > source
    assert collapse(cut, caller_limit=2).reason == ms.REASON_SOURCE_LIMIT                 # source > caller
    assert collapse(cut, caller_limit=2, stop=STOP_SAFE).reason == ms.REASON_SOURCE_LIMIT
    plain = [contribution(3, observed=400)]
    assert collapse(plain, caller_limit=2, inventory_gap=True).reason == ms.REASON_CALLER_LIMIT  # caller > gap
    assert collapse(plain, inventory_gap=True, source_newest=500.0).reason == \
        ms.REASON_PARTIAL_INVENTORY                                                         # gap > mismatch
    assert collapse(plain, source_newest=500.0).reason == ms.REASON_TIMESTAMP_MISMATCH


def test_a_source_cut_and_an_inventory_gap_coexist():
    coverage = collapse([contribution(3, observed=400, complete=200, truncated=True)],
                        inventory_gap=True)
    assert coverage.reason == ms.REASON_SOURCE_LIMIT
    assert coverage.complete_through is None          # the gap still removes the gap-free point
    assert coverage.observed_through == 400
    census = ProviderDiagnostics(readable=1, unknown=1, unavailable=2, unresolved_identities=0)
    assert (census.unknown, census.unavailable) == (1, 2)   # the gap stays visible, elsewhere
    import inspect
    assert "diagnostics" not in inspect.signature(ProviderResult.collapse).parameters


# --- collapse: freshness and mismatch ---------------------------------------------

def test_a_mismatch_inside_the_window_is_partial_and_stale():
    for end in (None, 900.0, 500.0):
        coverage = collapse([contribution(2, observed=400)], requested_end=end, source_newest=500.0)
        assert (coverage.status, coverage.reason) == (ms.COVERAGE_PARTIAL, ms.REASON_TIMESTAMP_MISMATCH)
        assert coverage.truncated is False
        assert coverage.freshness is ms.ReadFreshness.POTENTIALLY_STALE


def test_a_mismatch_outside_the_window_is_complete_and_stale():
    coverage = collapse([contribution(2, observed=400)], requested_end=450.0, source_newest=500.0)
    assert coverage.status == ms.COVERAGE_COMPLETE
    assert coverage.freshness is ms.ReadFreshness.POTENTIALLY_STALE


def test_an_absent_moment_is_unknown_with_no_structural_downgrade():
    assert collapse([contribution(2, observed=400)]).freshness is ms.ReadFreshness.UNKNOWN
    no_point = collapse([contribution(2, observed=None)], source_newest=500.0)
    assert no_point.freshness is ms.ReadFreshness.UNKNOWN
    assert no_point.status == ms.COVERAGE_COMPLETE


def test_freshness_is_computed_independently_of_the_reason():
    consistent = collapse([contribution(3, observed=400)], caller_limit=2, source_newest=400.0)
    assert consistent.reason == ms.REASON_CALLER_LIMIT
    assert consistent.freshness is ms.ReadFreshness.EVIDENCE_CONSISTENT
    assert collapse([contribution(1, observed=400)], source_newest=399.5).freshness is \
        ms.ReadFreshness.EVIDENCE_CONSISTENT
    stale_cut = collapse([contribution(3, observed=400, truncated=True)], source_newest=500.0)
    assert stale_cut.reason == ms.REASON_SOURCE_LIMIT
    assert stale_cut.freshness is ms.ReadFreshness.POTENTIALLY_STALE


def test_fractional_requested_bounds_survive_collapse_exactly():
    coverage = collapse([contribution(2, observed=140)], requested_start=100.25,
                        requested_end=200.75, source_newest=150.5)
    assert coverage.requested_start == 100.25 and coverage.requested_end == 200.75
    assert isinstance(coverage.requested_start, float) and isinstance(coverage.requested_end, float)
    assert coverage.reason == ms.REASON_TIMESTAMP_MISMATCH          # 150.5 > 140, inside 200.75
    assert type(coverage.observed_through) is int                    # provider evidence, uncast
    edge = collapse([contribution(2, observed=140)], requested_end=150.25, source_newest=150.5)
    assert edge.status == ms.COVERAGE_COMPLETE                       # 150.5 > 150.25: outside


# --- guards -------------------------------------------------------------------------

CASES = [dict(), dict(inventory_gap=True), dict(stop=STOP_UNSAFE), dict(caller_limit=2),
         dict(caller_limit=2, stop=STOP_SAFE), dict(source_newest=900.0),
         dict(source_newest=900.0, requested_end=500.0)]


def test_this_provider_never_emits_window_bound():
    for truncated in (False, True):
        for overrides in CASES:
            coverage = collapse([contribution(3, observed=400, truncated=truncated)], **overrides)
            assert coverage.reason != ms.REASON_WINDOW_BOUND
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "REASON_WINDOW_BOUND" not in names
    assert "ReadResult" not in names                  # production builds no ReadResult


def test_no_provider_vocabulary_reaches_the_envelope():
    for overrides in CASES:
        coverage = collapse([contribution(3, observed=400)], **overrides)
        for field in dataclasses.fields(coverage):
            value = getattr(coverage, field.name)
            if isinstance(value, str) and not isinstance(value, ms.ReadFreshness):
                assert value in ms.COVERAGE_STATUSES | ms.COVERAGE_REASONS, (field.name, value)
            else:
                assert isinstance(value, (ms.ReadFreshness, bool, int, float, type(None))), field.name
        rendered = repr(coverage)
        for leak in ("Msg_", "fixture", SESSION, "wxid_", "message_0", ".db"):
            assert leak not in rendered, leak
        assert "/" not in rendered


def test_the_bridge_crossing_is_exactly_two_modules():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    bridge_modules = {p.stem for p in (ROOT / "bridge").glob("*.py")}
    assert {"rion_reader_adapter", "store_access", "wechat_companion_mcp"} <= bridge_modules
    assert roots & bridge_modules == {"conversation_identity", "message_source"}
    for forbidden in ("bridge", "hashlib", "memory", "shadow", "ai", "core", "sqlite3"):
        assert forbidden not in roots, forbidden
    assert not any(r.startswith("memory") for r in roots)


def test_freshness_is_never_compared_against_a_clock():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    roots, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert roots.isdisjoint({"time", "datetime", "calendar"})
    assert names.isdisjoint({"now", "utcnow", "today", "monotonic", "perf_counter", "time"})
    # No age, tolerance or threshold constant: the only numbers are zero and one.
    numbers = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
               and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)}
    assert numbers <= {0, 1, 1.0}, numbers
