"""ShardRouter: every part is visited or excluded with a cause, and a stop is
classified against the traversal boundary, not the window.

Nothing here opens a database. Routing consumes ShardFacts, so every input is
built directly; keys are opaque strings and no name or path participates.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wechatdb.provider import discovery
from wechatdb.provider.discovery import (
    SHARD_KNOWN,
    SHARD_READABLE,
    SHARD_UNAVAILABLE,
    SHARD_UNKNOWN,
    ShardFacts,
    shard_key,
)
from wechatdb.provider.routing import (
    EXCLUDED_CONVERSATION_ABSENT,
    EXCLUDED_NOT_READABLE,
    EXCLUDED_OUT_OF_WINDOW,
    EXCLUSION_CAUSES,
    STOP_EXHAUSTED,
    STOP_KINDS,
    STOP_SAFE,
    STOP_UNSAFE,
    RoutePlan,
    ShardRouter,
)

PROVIDER = Path(discovery.__file__).resolve().parent

TABLE_A = "Msg_" + "0123456789abcdef" * 2
TABLE_B = "Msg_" + "fedcba9876543210" * 2


def readable(key, *, lo=None, hi=None, tables=(TABLE_A,)):
    """A READABLE fact: bounded when lo/hi are given, absent otherwise."""
    established = lo is not None
    return ShardFacts(key=key, state=SHARD_READABLE, bounds_established=established,
                      min_timestamp=lo, max_timestamp=hi, tables=tables)


def quiet(key, state):
    return ShardFacts(key=key, state=state, bounds_established=False,
                      min_timestamp=None, max_timestamp=None)


def inventory(*facts):
    return {f.key: f for f in facts}


def causes(plan):
    return dict(plan.exclusions)


ROUTER = ShardRouter()


# --- vocabulary ---------------------------------------------------------------

def test_the_vocabulary_is_closed_and_provider_internal():
    assert EXCLUSION_CAUSES == {"out_of_window", "not_readable", "conversation_absent"}
    assert STOP_KINDS == {"exhausted", "safe", "unsafe"}
    import message_source
    for name in ("EXCLUSION_CAUSES", "STOP_KINDS", "RoutePlan", "ShardRouter",
                 "STOP_SAFE", "EXCLUDED_OUT_OF_WINDOW"):
        assert not hasattr(message_source, name), name


# --- RoutePlan ----------------------------------------------------------------

def test_no_part_appears_twice_across_the_plan():
    RoutePlan(visit=("k_secret_a", "k_secret_b"),
              exclusions=(("k_secret_c", EXCLUDED_NOT_READABLE),))
    for bad in (
        dict(visit=("k_secret_a", "k_secret_a"), exclusions=()),
        dict(visit=(), exclusions=(("k_secret_c", EXCLUDED_NOT_READABLE),
                                   ("k_secret_c", EXCLUDED_OUT_OF_WINDOW))),
        dict(visit=("k_secret_a",), exclusions=(("k_secret_a", EXCLUDED_NOT_READABLE),)),
        dict(visit=("k_secret_a",), exclusions=(("k_secret_c", "vanished"),)),
    ):
        with pytest.raises(ValueError) as refusal:
            RoutePlan(**bad)
        # Fixed text, and neither the offending key nor the bad cause in it.
        assert "k_secret" not in str(refusal.value)
        assert "vanished" not in str(refusal.value)


def test_accounts_for_means_every_key_exactly_once_and_nothing_extra():
    inv = inventory(readable("a", lo=1, hi=2), quiet("b", SHARD_UNKNOWN))
    assert RoutePlan(("a",), (("b", EXCLUDED_NOT_READABLE),)).accounts_for(inv)
    assert not RoutePlan(("a",), ()).accounts_for(inv)                        # missing
    assert not RoutePlan(("a", "z"), (("b", EXCLUDED_NOT_READABLE),)).accounts_for(inv)  # extra
    assert RoutePlan((), ()).accounts_for({})


# --- planning -----------------------------------------------------------------

def test_every_part_is_either_visited_or_excluded_with_a_cause():
    inv = inventory(
        readable("r1", lo=100, hi=200), readable("r2"), readable("r3", lo=900, hi=999),
        quiet("k", SHARD_KNOWN), quiet("u", SHARD_UNKNOWN), quiet("x", SHARD_UNAVAILABLE),
    )
    plan = ROUTER.plan(inv, requested_start=150, requested_end=250)
    assert plan.accounts_for(inv)
    assert set(plan.visit) == {"r1", "r2"}
    assert causes(plan) == {"r3": EXCLUDED_OUT_OF_WINDOW, "k": EXCLUDED_NOT_READABLE,
                            "u": EXCLUDED_NOT_READABLE, "x": EXCLUDED_NOT_READABLE}
    assert all(c in EXCLUSION_CAUSES for c in causes(plan).values())


def test_exclusion_causes_follow_a_fixed_precedence():
    inv = inventory(
        ShardFacts(key="unread", state=SHARD_UNAVAILABLE, bounds_established=False,
                   min_timestamp=None, max_timestamp=None),
        readable("absent_and_disjoint", lo=1, hi=2, tables=(TABLE_B,)),
        readable("present_but_disjoint", lo=1, hi=2, tables=(TABLE_A,)),
        readable("present_and_overlapping", lo=100, hi=200, tables=(TABLE_A,)),
    )
    plan = ROUTER.plan(inv, requested_start=100, requested_end=200,
                       conversation_table=TABLE_A)
    assert causes(plan) == {
        "unread": EXCLUDED_NOT_READABLE,
        "absent_and_disjoint": EXCLUDED_CONVERSATION_ABSENT,
        "present_but_disjoint": EXCLUDED_OUT_OF_WINDOW,
    }
    assert plan.visit == ("present_and_overlapping",)


def test_only_a_readable_part_is_ever_visited():
    inv = inventory(quiet("k", SHARD_KNOWN), quiet("u", SHARD_UNKNOWN),
                    quiet("x", SHARD_UNAVAILABLE))
    plan = ROUTER.plan(inv, requested_start=None, requested_end=None)
    assert plan.visit == ()
    assert set(causes(plan).values()) == {EXCLUDED_NOT_READABLE}


def test_a_readable_part_without_established_bounds_is_always_visited():
    inv = inventory(readable("open"))
    for window in ((None, None), (0, 1), (10**9, 10**9 + 1)):
        plan = ROUTER.plan(inv, requested_start=window[0], requested_end=window[1])
        assert plan.visit == ("open",)


def test_window_overlap_is_inclusive_at_both_edges():
    inv = inventory(
        readable("ends_at_start", lo=50, hi=100),
        readable("starts_at_end", lo=200, hi=300),
        readable("just_before", lo=50, hi=99),
        readable("just_after", lo=201, hi=300),
        readable("inside", lo=120, hi=180),
    )
    plan = ROUTER.plan(inv, requested_start=100, requested_end=200)
    assert set(plan.visit) == {"ends_at_start", "starts_at_end", "inside"}
    assert causes(plan) == {"just_before": EXCLUDED_OUT_OF_WINDOW,
                            "just_after": EXCLUDED_OUT_OF_WINDOW}
    # Half-open requests: only the present bound constrains.
    assert set(ROUTER.plan(inv, requested_start=None, requested_end=99).visit) == \
        {"ends_at_start", "just_before"}
    assert set(ROUTER.plan(inv, requested_start=201, requested_end=None).visit) == \
        {"starts_at_end", "just_after"}


def test_unestablished_bounds_are_visited_first():
    inv = inventory(readable("bounded", lo=1, hi=10**9), readable("open_b"), readable("open_a"))
    plan = ROUTER.plan(inv, requested_start=None, requested_end=None)
    assert plan.visit == ("open_a", "open_b", "bounded")


def test_visit_order_is_newest_first_by_established_maximum_then_by_key():
    facts = [readable("b", lo=0, hi=300), readable("a", lo=0, hi=300),
             readable("c", lo=0, hi=500), readable("z", lo=0, hi=100),
             readable("m", lo=290, hi=300)]   # same max as a/b; min must not matter
    forward = {f.key: f for f in facts}
    backward = {f.key: f for f in reversed(facts)}
    expected = ("c", "a", "b", "m", "z")
    assert ROUTER.plan(forward, requested_start=None, requested_end=None).visit == expected
    assert ROUTER.plan(backward, requested_start=None, requested_end=None).visit == expected


def test_exclusion_order_is_deterministic_by_key():
    facts = [quiet("u3", SHARD_UNKNOWN), quiet("u1", SHARD_UNKNOWN), quiet("u2", SHARD_KNOWN)]
    forward = {f.key: f for f in facts}; backward = {f.key: f for f in reversed(facts)}
    assert ROUTER.plan(forward, requested_start=None, requested_end=None).exclusions == \
        ROUTER.plan(backward, requested_start=None, requested_end=None).exclusions == \
        (("u1", EXCLUDED_NOT_READABLE), ("u2", EXCLUDED_NOT_READABLE), ("u3", EXCLUDED_NOT_READABLE))


def test_a_conversation_scoped_read_excludes_parts_that_do_not_hold_it():
    inv = inventory(readable("holds", tables=(TABLE_A, TABLE_B)),
                    readable("lacks", tables=(TABLE_B,)))
    plan = ROUTER.plan(inv, requested_start=None, requested_end=None,
                       conversation_table=TABLE_A)
    assert plan.visit == ("holds",)
    assert causes(plan) == {"lacks": EXCLUDED_CONVERSATION_ABSENT}
    # No filter: both visit.
    assert set(ROUTER.plan(inv, requested_start=None, requested_end=None).visit) == {"holds", "lacks"}


def test_the_same_conversation_table_routes_to_every_part_that_holds_it():
    """The load-bearing multi-part case: one conversation, several shards."""
    inv = inventory(readable(shard_key("message_0.db"), lo=100, hi=200),
                    readable(shard_key("message_1.db"), lo=201, hi=300),
                    readable(shard_key("message_2.db")))
    plan = ROUTER.plan(inv, requested_start=150, requested_end=250,
                       conversation_table=TABLE_A)
    assert set(plan.visit) == {shard_key("message_0.db"), shard_key("message_1.db"),
                               shard_key("message_2.db")}
    assert plan.exclusions == ()


def test_invalid_conversation_scope_is_refused_not_treated_as_absent():
    inv = inventory(readable("r", tables=(TABLE_A,)))
    for bad in ("not a table", "Msg_deadbeef", "msg_" + "0" * 32, "Msg_" + "0" * 31,
                TABLE_A[len("Msg_"):], 64790855742931, b"Msg_" + b"0" * 32):
        with pytest.raises(ValueError) as refusal:
            ROUTER.plan(inv, requested_start=None, requested_end=None, conversation_table=bad)
        assert str(bad) not in str(refusal.value)


def test_conversation_table_is_never_the_generic_identifier():
    tree = ast.parse((PROVIDER / "routing.py").read_text(encoding="utf-8"))
    roots, names, attrs = set(), set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
    for forbidden in ("bridge", "message_source", "conversation_identity",
                      "rion_reader_adapter", "hashlib", "memory", "shadow", "ai", "core"):
        assert forbidden not in roots, forbidden
    assert "conversation_identifier" not in names
    assert "hash" not in names
    assert attrs.isdisjoint({"hexdigest", "digest", "blake2b", "sha256"})
    assert "CONVERSATION_TABLE" in names          # the parser-owned contract, not a copy
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "compile" for n in ast.walk(tree))


def test_inventory_mapping_keys_must_match_their_facts():
    forged = {"under_a_different_key": readable("real_key", lo=1, hi=2)}
    with pytest.raises(ValueError) as refusal:
        ROUTER.plan(forged, requested_start=None, requested_end=None)
    assert "real_key" not in str(refusal.value)


def test_an_inverted_requested_window_is_refused():
    inv = inventory(readable("r", lo=1, hi=2))
    with pytest.raises(ValueError):
        ROUTER.plan(inv, requested_start=200, requested_end=100)
    ROUTER.plan(inv, requested_start=100, requested_end=100)   # a single instant is fine


# --- stops --------------------------------------------------------------------

def stopped(inv, visited, oldest):
    plan = ROUTER.plan(inv, requested_start=None, requested_end=None)
    return plan, ROUTER.classify_stop(plan, inv, visited=visited, oldest_collected_at=oldest)


def test_visited_must_be_an_in_order_prefix_of_the_plan():
    inv = inventory(readable("k_secret_c", lo=0, hi=300), readable("k_secret_b", lo=0, hi=200),
                    readable("k_secret_a", lo=0, hi=100), quiet("k_secret_x", SHARD_UNKNOWN))
    plan = ROUTER.plan(inv, requested_start=None, requested_end=None)
    assert plan.visit == ("k_secret_c", "k_secret_b", "k_secret_a")
    for bad in (("k_secret_b",),                              # skipped the first step
                ("k_secret_b", "k_secret_c"),                 # out of order
                ("k_secret_c", "k_secret_c"),                 # duplicate
                ("k_secret_c", "nobody"),                     # unknown key
                ("k_secret_c", "k_secret_x"),                 # an exclusion key
                ("k_secret_c", "k_secret_b", "k_secret_a", "k_secret_a")):  # longer than the plan
        with pytest.raises(ValueError) as refusal:
            ROUTER.classify_stop(plan, inv, visited=bad, oldest_collected_at=50)
        assert "k_secret" not in str(refusal.value) and "nobody" not in str(refusal.value)
    # Every genuine prefix, including the empty one, classifies.
    for n in range(len(plan.visit) + 1):
        assert ROUTER.classify_stop(plan, inv, visited=plan.visit[:n],
                                    oldest_collected_at=50) in STOP_KINDS


def test_a_non_accounting_plan_cannot_be_classified():
    inv = inventory(readable("a", lo=0, hi=10), readable("b", lo=0, hi=5))
    forged = RoutePlan(visit=("a",), exclusions=())   # valid on its own, omits b
    with pytest.raises(ValueError) as refusal:
        ROUTER.classify_stop(forged, inv, visited=("a",), oldest_collected_at=None)
    assert "b" not in str(refusal.value).split()
    with pytest.raises(ValueError):   # mapping key must still equal facts.key
        ROUTER.classify_stop(RoutePlan(("wrong",), ()), {"wrong": readable("a", lo=0, hi=1)},
                             visited=("wrong",), oldest_collected_at=None)


def test_visiting_every_planned_part_is_exhausted_whatever_was_excluded():
    inv = inventory(readable("a", lo=0, hi=10), quiet("u", SHARD_UNKNOWN),
                    quiet("x", SHARD_UNAVAILABLE), quiet("k", SHARD_KNOWN))
    _, kind = stopped(inv, ("a",), None)
    assert kind == STOP_EXHAUSTED                      # even with nothing collected


def test_an_empty_visit_plan_is_exhausted():
    inv = inventory(quiet("u", SHARD_UNKNOWN))
    _, kind = stopped(inv, (), None)
    assert kind == STOP_EXHAUSTED
    assert stopped({}, (), None)[1] == STOP_EXHAUSTED


def test_a_stop_is_safe_only_below_the_oldest_collected_boundary():
    inv = inventory(readable("newest", lo=800, hi=900), readable("older", lo=400, hi=499),
                    readable("oldest", lo=100, hi=199))
    plan, kind = stopped(inv, ("newest",), 500)
    assert plan.visit == ("newest", "older", "oldest")
    assert kind == STOP_SAFE                            # 499 < 500 and 199 < 500


def test_an_unvisited_maximum_equal_to_the_boundary_is_unsafe():
    inv = inventory(readable("newest", lo=800, hi=900), readable("older", lo=400, hi=500))
    assert stopped(inv, ("newest",), 500)[1] == STOP_UNSAFE   # 500 < 500 is false
    assert stopped(inv, ("newest",), 501)[1] == STOP_SAFE


def test_a_stop_with_an_unbounded_unvisited_part_is_unsafe():
    """T-6a, the router half. Unbounded parts order first, so this stop has to
    skip past one deliberately -- and the router still refuses to call it safe."""
    inv = inventory(readable("open_a"), readable("open_b"), readable("bounded", lo=0, hi=10))
    plan = ROUTER.plan(inv, requested_start=None, requested_end=None)
    assert plan.visit == ("open_a", "open_b", "bounded")
    assert ROUTER.classify_stop(plan, inv, visited=("open_a",), oldest_collected_at=10**6) == STOP_UNSAFE


def test_a_stop_with_an_unvisited_maximum_after_the_boundary_is_unsafe():
    """T-6b, the router half: the comparison is against the traversal boundary."""
    inv = inventory(readable("newest", lo=800, hi=900), readable("later", lo=400, hi=600))
    assert stopped(inv, ("newest",), 500)[1] == STOP_UNSAFE


def test_a_stop_with_nothing_collected_is_unsafe():
    inv = inventory(readable("newest", lo=800, hi=900), readable("older", lo=100, hi=199))
    assert stopped(inv, ("newest",), None)[1] == STOP_UNSAFE
    assert stopped(inv, (), None)[1] == STOP_UNSAFE


def test_unknown_and_unavailable_parts_do_not_make_a_stop_unsafe():
    inv = inventory(readable("newest", lo=800, hi=900), readable("older", lo=100, hi=199),
                    quiet("u", SHARD_UNKNOWN), quiet("x", SHARD_UNAVAILABLE))
    assert stopped(inv, ("newest",), 500)[1] == STOP_SAFE
    assert stopped(inv, ("newest", "older"), 500)[1] == STOP_EXHAUSTED


def test_routing_constructs_no_coverage_and_knows_no_limit():
    source = (PROVIDER / "routing.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
            {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    for forbidden in ("ReadCoverage", "ReadResult", "ReadFreshness", "COVERAGE_COMPLETE",
                      "REASON_WINDOW_BOUND", "REASON_CALLER_LIMIT", "caller_limit"):
        assert forbidden not in names, forbidden
    for word in ("observed_complete", "window_bound", "caller_limit"):
        assert word not in source
