"""The ten construction invariants of :class:`ReadCoverage` (spec section 6.7).

A coverage claim is the one place this design can lie: every other field is
data a source read, while ``status`` is a statement *about* that read. So the
invariants are enforced where a statement is made rather than where it is
consumed -- a source that hit a cut-short condition cannot construct a complete
coverage, and the downgrade is therefore not a rule someone remembers to apply.

The tokens below are written out as literals rather than imported from the
module under test. A test that builds its expectations out of the
implementation's own constants proves only that the implementation agrees with
itself; these are transcribed from the spec's tables, so they disagree with the
module if either one drifts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from message_source import (  # noqa: E402
    ReadCoverage,
    ReadFreshness,
    ReadResult,
)

# --- The spec's vocabulary, transcribed -------------------------------------

COMPLETE = "observed_complete"
PARTIAL = "observed_partial"
UNAVAILABLE = "unavailable"
NOT_OBSERVED = "not_observed"

FULL_WINDOW_OBSERVED = "full_window_observed"
EMPTY_WINDOW = "empty_window"
CALLER_LIMIT = "caller_limit"
SOURCE_LIMIT = "source_limit"
WINDOW_BOUND = "window_bound"
UPSTREAM_MORE = "upstream_more"
PARTIAL_INVENTORY = "partial_inventory"
UNSAFE_EARLY_STOP = "unsafe_early_stop"
TIMESTAMP_MISMATCH = "timestamp_mismatch"
SCOPE_UNSUPPORTED = "scope_unsupported"
SCOPE_NOT_READ = "scope_not_read"
NO_OBSERVATION = "no_observation"


# --- Lawful baselines, one per status ----------------------------------------
#
# Each helper returns a claim that violates nothing, so a test can vary exactly
# one field and know which invariant it is aiming at.

def complete(**overrides):
    kwargs = dict(
        status=COMPLETE, reason=FULL_WINDOW_OBSERVED,
        requested_start=100, requested_end=200,
        observed_through=200, complete_through=200,
        freshness=ReadFreshness.EVIDENCE_CONSISTENT,
        truncated=False, item_count=3,
    )
    kwargs.update(overrides)
    return ReadCoverage(**kwargs)


def partial(**overrides):
    kwargs = dict(
        status=PARTIAL, reason=CALLER_LIMIT,
        requested_start=100, requested_end=200,
        observed_through=150, complete_through=None,
        freshness=ReadFreshness.EVIDENCE_CONSISTENT,
        truncated=True, item_count=50,
    )
    kwargs.update(overrides)
    return ReadCoverage(**kwargs)


def unavailable(**overrides):
    kwargs = dict(
        status=UNAVAILABLE, reason=SCOPE_UNSUPPORTED,
        requested_start=None, requested_end=None,
        observed_through=None, complete_through=None,
        freshness=ReadFreshness.UNKNOWN,
        truncated=False, item_count=0,
    )
    kwargs.update(overrides)
    return ReadCoverage(**kwargs)


def not_observed(**overrides):
    kwargs = dict(
        status=NOT_OBSERVED, reason=NO_OBSERVATION,
        requested_start=None, requested_end=None,
        observed_through=None, complete_through=None,
        freshness=ReadFreshness.UNKNOWN,
        truncated=False, item_count=0,
    )
    kwargs.update(overrides)
    return ReadCoverage(**kwargs)


# --- Invariant 1 -------------------------------------------------------------

def test_an_unknown_status_is_refused():
    """The vocabulary is closed, so an unrecognised claim is a defect."""
    with pytest.raises(ValueError):
        complete(status="observed_everything")

    with pytest.raises(ValueError):
        complete(status="")


# --- Invariant 2a ------------------------------------------------------------

def test_an_unknown_reason_is_refused():
    """A reason outside the closed set is the free text this design removed."""
    with pytest.raises(ValueError):
        partial(reason="the reader seemed unhappy")

    with pytest.raises(ValueError):
        partial(reason="")


# --- Invariant 2b ------------------------------------------------------------

#: Pairs read off the spec's section 6.5 table as *invalid*, chosen rather than
#: derived: asking the module which pairs it rejects would only prove it is
#: consistent with itself.
INVALID_PAIRS = [
    (COMPLETE, CALLER_LIMIT),
    (COMPLETE, SCOPE_UNSUPPORTED),
    (COMPLETE, NO_OBSERVATION),
    (PARTIAL, FULL_WINDOW_OBSERVED),
    (PARTIAL, EMPTY_WINDOW),
    (PARTIAL, WINDOW_BOUND),
    (PARTIAL, SCOPE_NOT_READ),
    (UNAVAILABLE, FULL_WINDOW_OBSERVED),
    (UNAVAILABLE, CALLER_LIMIT),
    (UNAVAILABLE, NO_OBSERVATION),
    (NOT_OBSERVED, EMPTY_WINDOW),
    (NOT_OBSERVED, PARTIAL_INVENTORY),
    (NOT_OBSERVED, SCOPE_UNSUPPORTED),
]


@pytest.mark.parametrize("status,reason", INVALID_PAIRS)
def test_a_reason_invalid_for_its_status_is_refused(status, reason):
    """A known word used for a claim it cannot explain is still a defect.

    ``not_observed`` paired with ``empty_window`` is the specific confusion the
    vocabulary exists to prevent: "observed and empty" is knowledge and "not
    observed" is its absence.
    """
    with pytest.raises(ValueError):
        ReadCoverage(
            status=status, reason=reason,
            requested_start=None, requested_end=None,
            observed_through=None, complete_through=None,
            freshness=ReadFreshness.UNKNOWN,
            truncated=False, item_count=0,
        )


# --- Invariant 3 -------------------------------------------------------------

def test_a_complete_read_can_never_be_truncated():
    """The structural form of the whole design.

    Not "a complete read should not be truncated" -- it cannot be, because the
    object refuses to exist. The reason here is one that *is* valid for
    ``observed_complete``, so nothing but invariant 3 can be what fires.
    """
    with pytest.raises(ValueError):
        complete(truncated=True)

    with pytest.raises(ValueError):
        complete(reason=WINDOW_BOUND, truncated=True)


# --- Invariant 4 -------------------------------------------------------------

def test_a_negative_item_count_is_refused():
    with pytest.raises(ValueError):
        partial(item_count=-1)

    with pytest.raises(ValueError):
        complete(item_count=-100)


# --- Invariant 5 -------------------------------------------------------------

def test_an_empty_window_reason_with_items_is_refused():
    """The trustworthy empty has to actually be empty."""
    with pytest.raises(ValueError):
        complete(reason=EMPTY_WINDOW, item_count=1)

    complete(reason=EMPTY_WINDOW, item_count=0)


# --- Invariant 6 -------------------------------------------------------------

#: Valid with ``observed_partial`` but *not* statements that the read was cut
#: short, so pairing either with ``truncated=True`` is incoherent.
NOT_CUT_SHORT = [TIMESTAMP_MISMATCH, PARTIAL_INVENTORY]


@pytest.mark.parametrize("reason", NOT_CUT_SHORT)
def test_truncation_requires_a_cut_short_reason(reason):
    """Truncation has to be explained by something that means truncation."""
    with pytest.raises(ValueError):
        partial(reason=reason, truncated=True)

    # The same reason without the claim of truncation is lawful, which is what
    # makes the failure above attributable to invariant 6 alone.
    partial(reason=reason, truncated=False)


@pytest.mark.parametrize(
    "reason", [CALLER_LIMIT, SOURCE_LIMIT, UPSTREAM_MORE, UNSAFE_EARLY_STOP])
def test_the_four_cut_short_reasons_permit_truncation(reason):
    assert partial(reason=reason, truncated=True).truncated is True


# --- Invariant 7 -------------------------------------------------------------

#: A status that served nothing carries no evidence about what was read. Each
#: entry varies exactly one field away from a lawful baseline.
NOTHING_CARRIED = [
    {"freshness": ReadFreshness.EVIDENCE_CONSISTENT},
    {"freshness": ReadFreshness.POTENTIALLY_STALE},
    {"item_count": 1},
    {"observed_through": 200},
    {"complete_through": 200},
]


@pytest.mark.parametrize("override", NOTHING_CARRIED)
@pytest.mark.parametrize("build", [unavailable, not_observed])
def test_unavailable_and_not_observed_carry_nothing(build, override):
    """Nothing was served, so there is nothing to say about the read.

    A freshness verdict, an item, or a boundary would each be a claim about
    material this read never reached.
    """
    with pytest.raises(ValueError):
        build(**override)


def test_the_other_valid_reasons_for_those_statuses_are_also_bound():
    """The invariant is about the status, not about one favoured reason."""
    with pytest.raises(ValueError):
        unavailable(reason=PARTIAL_INVENTORY, item_count=1)

    with pytest.raises(ValueError):
        not_observed(reason=SCOPE_NOT_READ, observed_through=200)


# --- Invariant 8a ------------------------------------------------------------

def test_a_complete_point_requires_an_observed_point():
    """Accounting for a window up to a moment you never looked at is a claim
    with nothing behind it."""
    with pytest.raises(ValueError):
        partial(observed_through=None, complete_through=150)


# --- Invariant 8b ------------------------------------------------------------

def test_a_complete_point_may_not_exceed_the_observed_point():
    with pytest.raises(ValueError):
        partial(observed_through=150, complete_through=151)

    with pytest.raises(ValueError):
        partial(observed_through=100, complete_through=9999)

    # Equal is lawful; only *exceeding* is the defect.
    assert partial(observed_through=150, complete_through=150) is not None


# --- Invariant 9 -------------------------------------------------------------

def test_a_complete_read_completes_through_what_it_observed():
    """If the whole window was accounted for, the two points coincide.

    A complete read that stops accounting short of what it looked at is
    describing a gap, which is what ``observed_partial`` is for.
    """
    with pytest.raises(ValueError):
        complete(observed_through=200, complete_through=None)

    with pytest.raises(ValueError):
        complete(observed_through=200, complete_through=100)

    # With no observed point there is nothing to coincide with, and the rule
    # does not apply.
    assert complete(observed_through=None, complete_through=None) is not None


# --- Invariant 10 ------------------------------------------------------------

def test_an_inverted_requested_window_is_a_caller_defect():
    """An inverted window is refused, never silently read as an empty one.

    Reinterpreting it would answer a question nobody asked and report the
    answer as complete.
    """
    with pytest.raises(ValueError):
        complete(requested_start=300, requested_end=100)

    with pytest.raises(ValueError):
        partial(requested_start=1, requested_end=0)

    # Equal bounds are a one-second window, not an inversion.
    assert complete(requested_start=200, requested_end=200) is not None

    # One bound absent means unbounded, and there is nothing to compare.
    assert complete(requested_start=None, requested_end=100) is not None
    assert complete(requested_start=100, requested_end=None) is not None


# --- Positive control --------------------------------------------------------

def test_a_lawful_coverage_constructs():
    """The invariants must refuse invalid states without forbidding valid ones.

    An over-tight invariant set is the failure mode that does not announce
    itself: every test above would still pass while no source could describe a
    read it actually performed.
    """
    complete_non_empty = ReadCoverage(
        status=COMPLETE, reason=FULL_WINDOW_OBSERVED,
        requested_start=100, requested_end=200,
        observed_through=200, complete_through=200,
        freshness=ReadFreshness.EVIDENCE_CONSISTENT,
        truncated=False, item_count=42,
    )
    assert complete_non_empty.status == COMPLETE
    assert complete_non_empty.item_count == 42

    trustworthy_empty = ReadCoverage(
        status=COMPLETE, reason=EMPTY_WINDOW,
        requested_start=100, requested_end=200,
        observed_through=200, complete_through=200,
        freshness=ReadFreshness.EVIDENCE_CONSISTENT,
        truncated=False, item_count=0,
    )
    assert trustworthy_empty.item_count == 0
    assert trustworthy_empty.truncated is False

    safe_early_stop = ReadCoverage(
        status=COMPLETE, reason=WINDOW_BOUND,
        requested_start=100, requested_end=200,
        observed_through=None, complete_through=None,
        freshness=ReadFreshness.EVIDENCE_CONSISTENT,
        truncated=False, item_count=7,
    )
    assert safe_early_stop.status == COMPLETE

    partial_truncated = ReadCoverage(
        status=PARTIAL, reason=CALLER_LIMIT,
        requested_start=None, requested_end=None,
        observed_through=150, complete_through=None,
        freshness=ReadFreshness.EVIDENCE_CONSISTENT,
        truncated=True, item_count=50,
    )
    assert partial_truncated.truncated is True

    moved_underneath = ReadCoverage(
        status=PARTIAL, reason=TIMESTAMP_MISMATCH,
        requested_start=100, requested_end=200,
        observed_through=180, complete_through=180,
        freshness=ReadFreshness.POTENTIALLY_STALE,
        truncated=False, item_count=9,
    )
    assert moved_underneath.freshness is ReadFreshness.POTENTIALLY_STALE

    unavailable_read = ReadCoverage(
        status=UNAVAILABLE, reason=SCOPE_UNSUPPORTED,
        requested_start=100, requested_end=200,
        observed_through=None, complete_through=None,
        freshness=ReadFreshness.UNKNOWN,
        truncated=False, item_count=0,
    )
    assert unavailable_read.status == UNAVAILABLE

    never_looked = ReadCoverage(
        status=NOT_OBSERVED, reason=SCOPE_NOT_READ,
        requested_start=None, requested_end=None,
        observed_through=None, complete_through=None,
        freshness=ReadFreshness.UNKNOWN,
        truncated=False, item_count=0,
    )
    assert never_looked.status == NOT_OBSERVED


# --- Shape -------------------------------------------------------------------

def test_the_claim_is_frozen_slotted_and_ordered_as_the_spec_states():
    """A claim cannot be edited after it was asserted, and carries no __dict__.

    Field order is part of the published shape: every source constructs these
    and a reordering would silently re-pair arguments at any call site using
    positional form.
    """
    import dataclasses

    assert [field.name for field in dataclasses.fields(ReadCoverage)] == [
        "status", "reason", "requested_start", "requested_end",
        "observed_through", "complete_through", "freshness", "truncated",
        "item_count",
    ]

    claim = complete()
    with pytest.raises(dataclasses.FrozenInstanceError):
        claim.status = PARTIAL
    assert not hasattr(claim, "__dict__")


def test_a_refusal_never_quotes_what_it_refused():
    """Messages are fixed tokens, not sentences built from the offending value.

    A message assembled from a field is how content, a path or a provider's
    own error text reaches a log through a validation error.
    """
    with pytest.raises(ValueError) as refusal:
        complete(status="wxid_a_real_looking_secret")
    assert "wxid_a_real_looking_secret" not in str(refusal.value)

    with pytest.raises(ValueError) as refusal:
        partial(reason="/Users/someone/Library/Containers/db.sqlite")
    assert "/" not in str(refusal.value)

    for case in (
        lambda: complete(status="nope"),
        lambda: partial(reason="nope"),
        lambda: complete(truncated=True),
        lambda: partial(item_count=-1),
        lambda: complete(reason=EMPTY_WINDOW, item_count=1),
        lambda: partial(reason=TIMESTAMP_MISMATCH, truncated=True),
        lambda: unavailable(item_count=1),
        lambda: partial(observed_through=None, complete_through=1),
        lambda: partial(observed_through=1, complete_through=2),
        lambda: complete(observed_through=200, complete_through=None),
        lambda: complete(requested_start=300, requested_end=100),
    ):
        with pytest.raises(ValueError) as refusal:
            case()
        message = str(refusal.value)
        assert message == message.lower(), message
        assert message.strip() == message, message
        assert message.isascii(), message


# --- ReadResult: invariant 11 ------------------------------------------------

def test_a_result_whose_count_disagrees_with_its_items_is_refused():
    """The claim and the items it describes have to be the same answer.

    A count that outruns the tuple is a source promising material it did not
    hand over; a count that falls short is one quietly not accounting for what
    it did. Both coverages below are lawful on their own, so the refusal can
    only be invariant 11.
    """
    claims_more = complete(item_count=3)
    with pytest.raises(ValueError):
        ReadResult(items=("a", "b"), coverage=claims_more)

    claims_fewer = complete(item_count=0, reason=FULL_WINDOW_OBSERVED)
    with pytest.raises(ValueError):
        ReadResult(items=("a", "b"), coverage=claims_fewer)

    # Agreement is all that was missing.
    assert ReadResult(items=("a", "b"), coverage=complete(item_count=2)).items \
        == ("a", "b")
    assert ReadResult(items=(), coverage=claims_fewer).items == ()


# --- ReadResult: invariant 12 ------------------------------------------------

def test_items_must_be_a_tuple_so_a_result_cannot_be_mutated():
    """A coverage asserted over a mutable sequence can be made false later.

    The list is refused rather than converted: silently coercing it would let a
    source keep a reference to the original and go on appending to it, which is
    the mutation the invariant exists to prevent. Making the immutable shape is
    the source's job.
    """
    lawful = complete(item_count=2)

    with pytest.raises(ValueError):
        ReadResult(items=["a", "b"], coverage=lawful)

    # Other sequences of the right length are refused for the same reason,
    # including ones that would have survived a len() check.
    with pytest.raises(ValueError):
        ReadResult(items=["a", "b"], coverage=complete(item_count=2))
    with pytest.raises(ValueError):
        ReadResult(items="ab", coverage=complete(item_count=2))

    assert ReadResult(items=("a", "b"), coverage=lawful).items == ("a", "b")


# --- ReadResult: migration affordances ---------------------------------------

def test_iteration_and_length_are_migration_affordances():
    """Enough for an existing list-shaped call site to keep working, no more.

    The durable shape is ``.items`` and ``.coverage``. Everything a list can do
    that is *not* here -- indexing, slicing, concatenation, equality with a
    list -- is absent on purpose: a consumer that only iterates is exactly the
    consumer this design exists to correct, so it has to be migrated rather
    than accommodated.
    """
    coverage = complete(item_count=3)
    result = ReadResult(items=("a", "b", "c"), coverage=coverage)

    assert list(result) == list(result.items)
    assert len(result) == len(result.items)
    assert result.coverage is coverage

    for forbidden in ("__getitem__", "get", "__bool__", "__contains__",
                      "__add__", "__reversed__"):
        assert forbidden not in vars(ReadResult), forbidden

    with pytest.raises(TypeError):
        result[0]

    assert result != ["a", "b", "c"]

    # Truthiness follows from __len__ rather than from a __bool__ of its own.
    # Worth pinning because it is the one list-like behaviour that survives,
    # and an empty result is emphatically not the same as an absent one: the
    # coverage beside it is what says whether the emptiness is knowledge.
    empty = ReadResult(items=(), coverage=complete(reason=EMPTY_WINDOW,
                                                   item_count=0))
    assert not empty
    assert empty.coverage.reason == EMPTY_WINDOW


# --- ReadResult: the shape actually works on this runtime --------------------

def test_a_generic_slotted_frozen_result_works_on_this_runtime():
    """`Generic` + `dataclass(frozen, slots)` is proved, not assumed.

    The combination has a version floor (spec section 6.4 names 3.11), and the
    failure mode if it were unsupported would be a confusing one at every
    future call site rather than here. So it is pinned once, on the interpreter
    the suite actually runs on.
    """
    import dataclasses
    import typing

    assert ReadResult[int] is not None
    assert typing.get_origin(ReadResult[int]) is ReadResult

    coverage = complete(item_count=2)
    result = ReadResult[int](items=(1, 2), coverage=coverage)

    assert isinstance(result, ReadResult)
    assert result.items == (1, 2)
    assert result.coverage is coverage
    assert len(result) == 2

    assert [field.name for field in dataclasses.fields(ReadResult)] == [
        "items", "coverage",
    ]

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.items = ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.coverage = complete(item_count=0)
    assert not hasattr(result, "__dict__")


def test_a_result_refusal_never_quotes_what_it_refused():
    """Same rule as the coverage refusals: fixed tokens, never the value."""
    for case in (
        lambda: ReadResult(items=("wxid_a_real_looking_secret",),
                           coverage=complete(item_count=2)),
        lambda: ReadResult(items=["/Users/someone/Library/db.sqlite"],
                           coverage=complete(item_count=1)),
    ):
        with pytest.raises(ValueError) as refusal:
            case()
        message = str(refusal.value)
        assert message == message.lower(), message
        assert message.isascii(), message
        assert "wxid" not in message
        assert "/" not in message


# --- The moment fields carry the precision their sources have ----------------

#: The four fields that hold a moment rather than a count or a token.
MOMENT_FIELDS = ("requested_start", "requested_end",
                 "observed_through", "complete_through")


def test_the_moment_fields_preserve_source_precision():
    """Unix seconds may be fractional, and this boundary translates nothing.

    Every moment a shipped source can supply is already a float --
    ``NormalizedMessage.first_observed_at``, ``NormalizedConversation``'s two
    boundaries, ``get_recent_messages``'s own lower bound -- and the memory
    record these fields are documented to copy into without a translation table
    is a float too. An ``int`` here would make that copy a rounding step, and a
    rounding step on a window bound moves the window.

    Checked against evaluated hints rather than the source text, so the
    annotation is pinned as the type it actually resolves to.
    """
    import typing

    hints = typing.get_type_hints(ReadCoverage)
    for name in MOMENT_FIELDS:
        assert hints[name] == (float | None), (name, hints[name])

    # Nothing else moved: the two non-moment fields keep their own types.
    assert hints["item_count"] is int
    assert hints["truncated"] is bool


def test_a_fractional_moment_survives_construction_unchanged():
    """The value read back is the value handed in, to the bit.

    Asserting the exact float rather than merely that construction succeeded:
    a silent ``int()`` would still construct, and would still satisfy every
    invariant, while quietly moving a window bound by up to a second.
    """
    coverage = complete(
        requested_start=100.25, requested_end=200.75,
        observed_through=200.75, complete_through=200.75,
    )

    assert coverage.requested_start == 100.25
    assert coverage.requested_end == 200.75
    assert coverage.observed_through == 200.75
    assert coverage.complete_through == 200.75

    for name in MOMENT_FIELDS:
        value = getattr(coverage, name)
        assert isinstance(value, float), name
        assert value != int(value), name

    # An integer-valued source stays valid evidence; this is about precision
    # that exists, not about requiring precision that does not.
    integral = complete(requested_start=100, requested_end=200,
                        observed_through=200, complete_through=200)
    assert integral.observed_through == 200


def test_the_invariants_still_hold_on_fractional_moments():
    """The comparisons in __post_init__ need no numeric type of their own."""
    with pytest.raises(ValueError):
        complete(requested_start=200.75, requested_end=200.25)

    with pytest.raises(ValueError):
        partial(observed_through=150.25, complete_through=150.75)

    with pytest.raises(ValueError):
        complete(observed_through=200.75, complete_through=200.25)

    assert partial(observed_through=150.5,
                   complete_through=150.5).complete_through == 150.5
