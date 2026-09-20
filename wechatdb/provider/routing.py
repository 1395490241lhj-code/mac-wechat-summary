"""Routing: which parts a read must touch, and whether stopping early was safe.

Two questions, each answered with total accounting. ``plan`` decides, for
every part in an inventory, exactly one of: visit it, or exclude it with one
fixed cause. Nothing leaves the inventory without a recorded reason, and the
cause is chosen by a fixed precedence so no part ever gets an arbitrary one.
``classify_stop`` then says whether a traversal that stopped early can trust
what it already has.

A stop is classified against the traversal boundary, not the requested window.
The planner has already excluded every bounded part that misses the window, so
what is left to ask is whether any unvisited planned part could still hold a
record newer than or equal to the oldest one collected so far. If none can,
skipping them cannot change the newest-first limited answer in hand. That is
all a safe stop means. It says nothing about completeness; what coverage a
safe stop becomes is decided elsewhere, with the evidence about the limit that
this module deliberately does not have.

Conversation scope is the exact parser table name. It is tested by membership
in a part's recognised tables and is never hashed, stripped, converted, or
compared with the generic conversation identifier, which is a different digest
of a different thing. The same table legitimately lives in several parts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from wechatdb.parser import CONVERSATION_TABLE

from .discovery import SHARD_READABLE, ShardFacts

# -- vocabulary, provider-internal only ----------------------------------------

EXCLUDED_OUT_OF_WINDOW = "out_of_window"
EXCLUDED_NOT_READABLE = "not_readable"
EXCLUDED_CONVERSATION_ABSENT = "conversation_absent"
EXCLUSION_CAUSES = frozenset({
    EXCLUDED_OUT_OF_WINDOW,
    EXCLUDED_NOT_READABLE,
    EXCLUDED_CONVERSATION_ABSENT,
})

STOP_EXHAUSTED = "exhausted"
STOP_SAFE = "safe"
STOP_UNSAFE = "unsafe"
STOP_KINDS = frozenset({
    STOP_EXHAUSTED,
    STOP_SAFE,
    STOP_UNSAFE,
})


# -- the plan -------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class RoutePlan:
    """Every part, exactly once: visited in order, or excluded with one cause.

    A plan that could name a part twice, or both visit and exclude it, or
    excuse it with a word outside the vocabulary, is refused at construction.
    """

    visit: tuple[str, ...]
    exclusions: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if len(set(self.visit)) != len(self.visit):
            raise ValueError("a part is planned twice")
        excluded = [key for key, _ in self.exclusions]
        if len(set(excluded)) != len(excluded):
            raise ValueError("a part is excluded twice")
        if set(excluded) & set(self.visit):
            raise ValueError("a part is both planned and excluded")
        if any(cause not in EXCLUSION_CAUSES for _, cause in self.exclusions):
            raise ValueError("exclusion cause is not in the closed vocabulary")

    def accounts_for(self, inventory: Mapping[str, ShardFacts]) -> bool:
        """True only when every inventory key appears exactly once, and no
        other key appears at all."""
        planned = set(self.visit) | {key for key, _ in self.exclusions}
        return (planned == set(inventory)
                and len(self.visit) + len(self.exclusions) == len(inventory))


# -- the router -----------------------------------------------------------------

def _check_inventory(inventory: Mapping[str, ShardFacts]) -> None:
    """Evidence is routed only under the key it claims for itself."""
    for key, facts in inventory.items():
        if facts.key != key:
            raise ValueError("inventory key does not match its facts")


def _disjoint(facts: ShardFacts, start: float | None, end: float | None) -> bool:
    """An established, inclusive interval that cannot meet the inclusive request.

    Equality at either edge overlaps. Only a bound that is present constrains.
    """
    if start is not None and facts.max_timestamp < start:
        return True
    if end is not None and facts.min_timestamp > end:
        return True
    return False


class ShardRouter:
    """Plans a traversal over an inventory, and classifies where it stopped."""

    def plan(
        self,
        inventory: Mapping[str, ShardFacts],
        *,
        requested_start: float | None,
        requested_end: float | None,
        conversation_table: str | None = None,
    ) -> RoutePlan:
        """Visit or exclude every part, by fixed precedence.

        The requested bounds are the caller's, in the generic contract's float
        seconds, and are compared exactly as given: never rounded, floored or
        cast. A part's own bounds stay integer provider evidence.

        Not readable; else the requested conversation table is absent; else
        established bounds are disjoint from the window; else visit. A readable
        part with no established bounds can never satisfy the third test and
        therefore always visits. Order is deterministic and independent of the
        mapping's insertion order: unbounded readable parts first by opaque
        key, then bounded parts newest-first by maximum with the opaque key as
        the only tie-break. No name or path is consulted.
        """
        _check_inventory(inventory)
        if (requested_start is not None and requested_end is not None
                and requested_start > requested_end):
            raise ValueError("requested window is inverted")
        if conversation_table is not None and (
                not isinstance(conversation_table, str)
                or CONVERSATION_TABLE.fullmatch(conversation_table) is None):
            raise ValueError("conversation scope is not a conversation table name")

        unbounded: list[ShardFacts] = []
        bounded: list[ShardFacts] = []
        excluded: list[tuple[str, str]] = []
        for key in sorted(inventory):
            facts = inventory[key]
            if facts.state != SHARD_READABLE:
                excluded.append((key, EXCLUDED_NOT_READABLE))
            elif conversation_table is not None and conversation_table not in facts.tables:
                excluded.append((key, EXCLUDED_CONVERSATION_ABSENT))
            elif facts.bounds_established:
                if _disjoint(facts, requested_start, requested_end):
                    excluded.append((key, EXCLUDED_OUT_OF_WINDOW))
                else:
                    bounded.append(facts)
            else:
                unbounded.append(facts)
        bounded.sort(key=lambda facts: (-facts.max_timestamp, facts.key))
        visit = tuple(facts.key for facts in unbounded) + tuple(facts.key for facts in bounded)
        return RoutePlan(visit=visit, exclusions=tuple(excluded))

    def classify_stop(
        self,
        plan: RoutePlan,
        inventory: Mapping[str, ShardFacts],
        *,
        visited: Sequence[str],
        oldest_collected_at: int | None,
    ) -> str:
        """Exhausted, safe, or unsafe -- against the traversal boundary.

        ``visited`` must be an in-order prefix of the plan; anything else is
        refused rather than classified, because the answer depends on order.
        Exhausted when every planned part was visited, whatever was excluded.
        Safe only for an actual early stop with a known boundary, where every
        unvisited planned part is bounded and its maximum lies strictly below
        that boundary. Everything else is unsafe. Excluded parts play no role
        here; what an inventory gap means for coverage is decided elsewhere.
        """
        _check_inventory(inventory)
        if not plan.accounts_for(inventory):
            raise ValueError("plan does not account for the inventory")
        seen = tuple(visited)
        if len(seen) > len(plan.visit) or seen != plan.visit[:len(seen)]:
            raise ValueError("visited is not a prefix of the plan")
        if seen == plan.visit:
            return STOP_EXHAUSTED
        if oldest_collected_at is None:
            return STOP_UNSAFE
        for key in plan.visit[len(seen):]:
            facts = inventory[key]
            if not facts.bounds_established or facts.max_timestamp >= oldest_collected_at:
                return STOP_UNSAFE
        return STOP_SAFE
