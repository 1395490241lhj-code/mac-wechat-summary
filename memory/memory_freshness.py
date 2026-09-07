"""Freshness: when memory was last updated, and through what point a source
was observed. Derived, deterministically, from the persisted run and coverage
state; never from a clock except for ``generated_at``.

Freshness is not coverage, and neither is "the newest message". The three
answer different questions and this module keeps them apart on purpose:

* **Coverage** — what portion of the *requested* data was observed. Lives in
  ``memory_store.assess_coverage`` and travels with every query result.
* **Freshness** — when memory was last synced, and up to what moment the
  source was looked at. This module.
* **Latest message** — the newest stored message timestamp. Reported here,
  labelled distinctly, because a source that was observed through 09:58 and
  whose newest message is from 09:42 has *no messages between 09:42 and
  09:58*, which is a fact, not staleness.

There is deliberately no ``is_fresh`` boolean and no staleness threshold. A
sync from yesterday with complete coverage through yesterday is exactly that;
whether it is "too old" is a product or agent decision made with the
timestamps in view, not a verdict this layer hands down.

Timestamp semantics, exactly
----------------------------

``last_attempted_at``
    ``started_at`` of the most recent run for the source, whatever its state.
``last_succeeded_at``
    ``completed_at`` of the most recent *succeeded* run. A later failure does
    not move it: the last known-good boundary survives, beside the failure.
``observed_through``
    The latest moment the source is known to have been looked at: over every
    coverage record from a succeeded run that is ``observed_complete`` or
    ``observed_partial``, the record's ``window_end`` -- or, for a record with
    no upper bound, the run's ``completed_at``, because an unbounded read
    covers everything that existed when it ran. Unavailable records
    contribute nothing: a refusal observed nothing.
``complete_through``
    The same, over ``observed_complete`` records only.
``latest_message_at``
    ``MAX(timestamp)`` over the source's stored messages, with its
    ``timestamp_kind`` so a reader knows whether it is when a screen was
    first seen or when the source says the message was created.

Aggregate boundaries are the *minimum* over participating sources -- the
point through which *every* source is known -- and are ``None`` when any
source has none. Caveats name every source whose state a reader should see.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from memory_store import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    RUN_SUCCEEDED,
    MemoryStore,
)


@dataclass(frozen=True)
class SourceFreshness:
    source: str
    runs_total: int
    last_attempted_at: float | None
    last_attempt_state: str | None
    last_attempt_failure_state: str | None
    last_succeeded_at: float | None
    observed_through: float | None
    complete_through: float | None
    latest_message_at: float | None
    latest_message_timestamp_kind: str | None
    stored_messages: int

    @property
    def has_succeeded(self) -> bool:
        return self.last_succeeded_at is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "runs_total": self.runs_total,
            "last_attempted_at": self.last_attempted_at,
            "last_attempt_state": self.last_attempt_state,
            "last_attempt_failure_state": self.last_attempt_failure_state,
            "last_succeeded_at": self.last_succeeded_at,
            "observed_through": self.observed_through,
            "complete_through": self.complete_through,
            "latest_message_at": self.latest_message_at,
            "latest_message_timestamp_kind": self.latest_message_timestamp_kind,
            "stored_messages": self.stored_messages,
        }


@dataclass(frozen=True)
class MemoryFreshness:
    generated_at: float
    sources: tuple[SourceFreshness, ...]
    caveats: tuple[str, ...] = field(default_factory=tuple)

    @property
    def participating_sources(self) -> tuple[str, ...]:
        return tuple(s.source for s in self.sources)

    @property
    def last_successful_sync(self) -> tuple[str, float] | None:
        """The most recent successful run across sources, and whose it was."""
        successes = [(s.last_succeeded_at, s.source) for s in self.sources if s.last_succeeded_at is not None]
        if not successes:
            return None
        at, source = max(successes)
        return source, at

    @property
    def observed_through_all_sources(self) -> float | None:
        values = [s.observed_through for s in self.sources]
        if not values or any(v is None for v in values):
            return None
        return min(values)  # type: ignore[type-var]

    @property
    def complete_through_all_sources(self) -> float | None:
        values = [s.complete_through for s in self.sources]
        if not values or any(v is None for v in values):
            return None
        return min(values)  # type: ignore[type-var]

    def as_dict(self) -> dict[str, Any]:
        last = self.last_successful_sync
        return {
            "generated_at": self.generated_at,
            "participating_sources": list(self.participating_sources),
            "last_successful_sync": (
                {"source": last[0], "at": last[1]} if last else None
            ),
            "observed_through_all_sources": self.observed_through_all_sources,
            "complete_through_all_sources": self.complete_through_all_sources,
            "sources": {s.source: s.as_dict() for s in self.sources},
            "caveats": list(self.caveats),
            "semantics": {
                "observed_through": "latest moment the source is known to have been looked at",
                "complete_through": "latest moment through which coverage was complete",
                "latest_message_at": "newest stored message timestamp; not a freshness boundary",
                "last_succeeded_at": "when the last successful sync finished; a later failure does not move it",
            },
        }


def source_freshness(store: MemoryStore, source: str) -> SourceFreshness:
    connection = store.connection
    runs = connection.execute(
        "SELECT run_id, started_at, completed_at, state, failure_state FROM ingestion_runs"
        " WHERE source = ? ORDER BY started_at DESC, run_id DESC;",
        (source,),
    ).fetchall()
    latest = runs[0] if runs else None
    succeeded = [r for r in runs if r["state"] == RUN_SUCCEEDED]
    last_success = succeeded[0] if succeeded else None
    observed: list[float] = []
    complete: list[float] = []
    if succeeded:
        by_run = {r["run_id"]: r for r in succeeded}
        placeholders = ",".join("?" for _ in by_run)
        for row in connection.execute(
            f"SELECT run_id, status, window_end FROM coverage WHERE source = ? AND run_id IN ({placeholders});",
            (source, *by_run),
        ):
            if row["status"] not in (COVERAGE_COMPLETE, COVERAGE_PARTIAL):
                continue
            run = by_run[row["run_id"]]
            boundary = row["window_end"] if row["window_end"] is not None else run["completed_at"]
            if boundary is None:
                continue
            observed.append(float(boundary))
            if row["status"] == COVERAGE_COMPLETE:
                complete.append(float(boundary))
    newest = connection.execute(
        "SELECT timestamp, timestamp_kind FROM messages WHERE source = ?"
        " ORDER BY timestamp DESC, canonical_id ASC LIMIT 1;",
        (source,),
    ).fetchone()
    count = int(connection.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE source = ?;", (source,)
    ).fetchone()["n"])
    return SourceFreshness(
        source=source,
        runs_total=len(runs),
        last_attempted_at=latest["started_at"] if latest else None,
        last_attempt_state=latest["state"] if latest else None,
        last_attempt_failure_state=latest["failure_state"] if latest else None,
        last_succeeded_at=last_success["completed_at"] if last_success else None,
        observed_through=max(observed) if observed else None,
        complete_through=max(complete) if complete else None,
        latest_message_at=newest["timestamp"] if newest else None,
        latest_message_timestamp_kind=newest["timestamp_kind"] if newest else None,
        stored_messages=count,
    )


def memory_freshness(store: MemoryStore, *, generated_at: float) -> MemoryFreshness:
    """Freshness for every source that has ever run, or stored anything."""
    names = sorted({
        *(r["source"] for r in store.connection.execute("SELECT DISTINCT source FROM ingestion_runs;")),
        *(r["source"] for r in store.connection.execute("SELECT DISTINCT source FROM messages;")),
    })
    sources = tuple(source_freshness(store, n) for n in names)
    caveats: list[str] = []
    if not sources:
        caveats.append("no_ingestion_history")
    for s in sources:
        if s.last_attempt_state != RUN_SUCCEEDED and s.last_attempt_state is not None:
            caveats.append(f"{s.source}:last_attempt_{s.last_attempt_state}"
                           + (f":{s.last_attempt_failure_state}" if s.last_attempt_failure_state else ""))
        if not s.has_succeeded:
            caveats.append(f"{s.source}:never_succeeded")
        elif s.observed_through is None:
            caveats.append(f"{s.source}:no_observed_window")
        elif s.complete_through is None:
            caveats.append(f"{s.source}:no_complete_window")
        elif s.complete_through < s.observed_through:
            caveats.append(f"{s.source}:complete_before_observed")
    return MemoryFreshness(generated_at=generated_at, sources=sources, caveats=tuple(dict.fromkeys(caveats)))


__all__ = ["MemoryFreshness", "SourceFreshness", "memory_freshness", "source_freshness"]
