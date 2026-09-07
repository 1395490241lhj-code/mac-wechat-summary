"""The one explicit way to put messages into memory: a foreground sync.

There is no scheduler here, no polling, no daemon and no hook. A sync happens
when an operator runs it -- from a shell, or one day from an app action -- and
it runs once, in the foreground, and returns. It cannot be started by an agent:
nothing in ``bridge/`` or ``shadow/`` imports this module, the MCP bridge has
no write or action tool, and the memory layer's import allowlist keeps it that
way.

It is consent-gated by construction: the store is opened through
:func:`memory_consent.resolve_consent`, so every refusal that gate can make is
a refusal here, and there is no path around it. Ingestion itself is the M1
ingestor, so a repeated sync is idempotent and a failed one writes nothing.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from memory_consent import (
    ConsentDecision,
    ConsentStateReader,
    read_app_consent_state_macos,
    resolve_consent,
)
from memory_freshness import memory_freshness
from memory_ingest import IngestionReport, MemoryIngestor
from memory_store import MemoryStore
from message_source import MessageSource, MessageSourceError


@dataclass(frozen=True)
class SyncReport:
    """Counts and fixed tokens only. Never content, never a path."""

    consent: ConsentDecision
    ingestion: IngestionReport | None
    #: Freshness after the run, as the wire reports it. Counts and
    #: timestamps only.
    freshness: dict | None = None

    @property
    def ok(self) -> bool:
        return self.ingestion is not None and self.ingestion.succeeded

    def summary(self) -> dict[str, object]:
        if self.ingestion is None:
            return {"ok": False, "state": self.consent.state, "detail": self.consent.detail}
        report = self.ingestion
        return {
            "ok": report.succeeded,
            "state": report.state,
            "source": report.source,
            "run_id": report.run_id,
            "conversations_seen": report.conversations_seen,
            "messages_seen": report.messages_seen,
            "messages_inserted": report.messages_inserted,
            "messages_updated": report.messages_updated,
            "duplicates_detected": report.duplicates_detected,
            "failure_state": report.failure_state,
            "consent_generation": self.consent.consent_generation,
            "freshness": self.freshness,
        }


def sync_from_source(
    source: MessageSource,
    *,
    environment: Mapping[str, str] | None = None,
    read_app_consent_state: ConsentStateReader = read_app_consent_state_macos,
    conversation_limit: int = 50,
    message_limit: int = 200,
) -> SyncReport:
    """One foreground ingestion of one source, or a refusal.

    A refused consent returns a report with ``ingestion`` ``None`` and the
    gate's state; it does not raise, so a CLI can print the reason and exit
    non-zero without a traceback carrying anything.
    """
    decision = resolve_consent(environment, read_app_consent_state)
    if not decision.allowed:
        return SyncReport(consent=decision, ingestion=None)
    with MemoryStore.open(decision) as store:
        report = MemoryIngestor(store).ingest_from_source(
            source, conversation_limit=conversation_limit, message_limit=message_limit
        )
        freshness = memory_freshness(store, generated_at=time.time()).as_dict()
    return SyncReport(consent=decision, ingestion=report, freshness=freshness)


def build_selected_source() -> MessageSource:
    """The source the environment selects, through the bridge's own resolver.

    Imported lazily so this module -- and its tests -- never need the MCP SDK
    the bridge module pulls in at import time. ``active_source`` is the
    bridge's rule: absent selection means the visual store, ``database``
    requires an injected reader, an unknown name fails closed, and there is
    **no fallback in either direction** -- a database selection whose reader
    is missing raises here and the sync does not run against the visual
    store instead. The bridge's own opt-ins still apply.
    """
    import os

    sys.path.insert(
        0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bridge")
    )
    from wechat_companion_mcp import active_source  # noqa: PLC0415

    return active_source()


def main(
    argv: list[str] | None = None,
    *,
    build_source: Callable[[], MessageSource] = build_selected_source,
) -> int:
    parser = argparse.ArgumentParser(
        prog="wechat-memory-sync",
        description=(
            "One explicit, foreground sync of the visual store into local memory. "
            "Consent-gated; no scheduling; prints counts only."
        ),
    )
    parser.add_argument("--conversation-limit", type=int, default=50)
    parser.add_argument("--message-limit", type=int, default=200)
    arguments = parser.parse_args(argv)
    try:
        source = build_source()
    except MessageSourceError as error:
        # The selected source cannot be built. Say so and stop; never sync a
        # different source than the one that was selected.
        print(f"ok: False\nstate: {error.state}\ndetail: {error.detail}")
        return 1
    report = sync_from_source(
        source,
        conversation_limit=arguments.conversation_limit,
        message_limit=arguments.message_limit,
    )
    for key, value in report.summary().items():
        print(f"{key}: {value}")
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
