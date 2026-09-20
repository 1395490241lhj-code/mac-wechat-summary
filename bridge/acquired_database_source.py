"""The production bridge from acquisition into the database provider.

This is the one module that may depend on the acquisition package and the
schema provider, and it is both halves of that crossing:

* open_database_source is the **production composition root**. It reads the
  recorded database source decision, and when one is recorded it builds the
  coordinator and returns a source backed by the acquisition Fast Lane.
  Nothing here searches for WeChat data: the decision was made and recorded
  earlier, and this module only consumes it.
* AcquiredDatabaseSource builds and fully consumes one provider inside each
  acquisition lease, so no decrypted handle, SQLite connection or workspace
  outlives the operation that needed it.

Two boundaries are load-bearing.

**Visual fallback happens before a database result escapes, or not at all.**
An acquisition that never became READY, or a read that failed before it
returned anything, may be answered by the visual store instead. Once a
database answer exists, a later failure is an error and never a substitution.
A reference the database minted is never reinterpreted by the visual store,
which is why such a follow-up never takes the fallback path.

**No exception text crosses this boundary.** A known typed refusal keeps its
fixed mapping; anything else becomes one fixed, content-free token. A SQLite
message, a path, a key error or a chat identifier must not reach a client
through an error string.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

try:
    from acquisition import (
        AcquisitionCoordinator,
        AcquisitionState,
        KeyStore,
        SourceLocator,
    )
    from acquisition.coordinator import AcquisitionCleanupError
except ImportError:  # pragma: no cover - bridge launched from another cwd
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from acquisition import (
        AcquisitionCoordinator,
        AcquisitionState,
        KeyStore,
        SourceLocator,
    )
    from acquisition.coordinator import AcquisitionCleanupError
from message_source import (
    SOURCE_DATABASE,
    SOURCE_VISUAL,
    MessageSource,
    MessageSourceError,
    ReadResult,
    SourceStatus,
    database_conversation_id,
    database_conversation_reference,
)
from wechatdb.provider import (
    ExplicitShardLocator,
    IdentityCatalog,
    ReadOnlySqliteOpener,
    ShardEntry,
    ShardedMessageProvider,
    UnsupportedGeneration,
)

# --- Where the app keeps its own state ---------------------------------------
#
# The recorded decision and the acquisition workspace are the *product's* own
# files, not WeChat's. They live under the app's Application Support directory,
# beside the message and memory stores the macOS app already writes. A test
# pins the three spellings of that directory name together.

#: The app's own directory inside Application Support.
APP_SUPPORT_COMPONENTS: tuple[str, ...] = (
    "Library", "Application Support", "WeChatCompanion",
)

#: The recorded database source decision. Private configuration: it names local
#: filesystem paths, and never key material.
RECORDED_SOURCE_FILE_NAME: str = "database_source.json"

#: Product-owned scratch for acquisition leases, swept on every use.
WORKSPACE_DIRECTORY_NAME: str = "acquisition"

#: The fixed, content-free state for a database failure this layer could not
#: classify. Deliberately not a string built from the failure.
UNCLASSIFIED_FAILURE: str = "database_unavailable"

#: The fixed, content-free state for a conversation reference that does not
#: belong to the database source.
REFERENCE_MISMATCH: str = "conversation_reference_mismatch"


def _app_support(home: str | os.PathLike[str] | None = None) -> Path:
    base = Path(home).expanduser() if home is not None else Path.home()
    return base.joinpath(*APP_SUPPORT_COMPONENTS)


def recorded_manifest_path(home: str | os.PathLike[str] | None = None) -> Path:
    """The app-owned path of the recorded source decision. Creates nothing."""
    return _app_support(home) / RECORDED_SOURCE_FILE_NAME


def acquisition_workspace_root(home: str | os.PathLike[str] | None = None) -> Path:
    """The app-owned root of acquisition leases. Creates nothing."""
    return _app_support(home) / WORKSPACE_DIRECTORY_NAME


class _DatabaseUnavailable(MessageSourceError):
    """A database read that produced no answer. Fixed copy, never a payload."""

    def __init__(self, state: str = UNCLASSIFIED_FAILURE) -> None:
        super().__init__(state, "The database source is not safely readable.")


def open_database_source(
    visual_factory: Callable[[], MessageSource],
    *,
    home: str | os.PathLike[str] | None = None,
    key_store: object | None = None,
    decryptor: object | None = None,
) -> MessageSource | None:
    """The production composition root for the database source.

    Reads the one recorded decision. A well-formed record yields the
    acquisition-backed source; no record, an unsupported record, or a malformed
    one yields None and the caller decides what that means. Database mode that
    was never established is not an error.
    """
    located = SourceLocator(recorded_manifest_path(home)).resolve()
    if located.source_set is None:
        return None
    try:
        coordinator = AcquisitionCoordinator(
            key_store if key_store is not None else KeyStore(),
            acquisition_workspace_root(home),
            decryptor=decryptor,
        )
    except Exception:
        # No usable key store or workspace: database mode is simply not
        # available here. A fixed outcome, never the failure's text.
        return None
    return AcquiredDatabaseSource(coordinator, located.source_set, visual_factory)


def _tagged_conversations(result: ReadResult) -> ReadResult:
    return ReadResult(
        items=tuple(
            replace(item, id=database_conversation_reference(item.id))
            for item in result.items
        ),
        coverage=result.coverage,
    )


def _tagged_messages(result: ReadResult) -> ReadResult:
    return ReadResult(
        items=tuple(
            replace(
                item,
                conversation_id=database_conversation_reference(item.conversation_id),
            )
            for item in result.items
        ),
        coverage=result.coverage,
    )


class AcquiredDatabaseSource:
    """Build and fully consume one provider inside each acquisition lease."""

    name: str = SOURCE_DATABASE

    def __init__(
        self,
        coordinator: AcquisitionCoordinator,
        source_set: object,
        visual_factory: Callable[[], MessageSource],
    ) -> None:
        self._coordinator = coordinator
        self._source_set = source_set
        self._visual_factory = visual_factory

    # -- one acquisition, one operation --------------------------------------

    def _database(self, consume: Callable[[object], object]) -> object:
        with self._coordinator.prepare(
            self._source_set, database_mode_enabled=True
        ) as outcome:
            if outcome.readiness.state is not AcquisitionState.READY:
                raise _DatabaseUnavailable(outcome.readiness.state.value)
            prepared = outcome.prepared_source
            assert prepared is not None
            entries = tuple(
                ShardEntry("message_{}.db".format(index), handle.value)
                for index, handle in enumerate(prepared.message_handles)
            )
            opener = ReadOnlySqliteOpener()
            catalog = IdentityCatalog.build(
                opener=opener,
                session=(
                    ShardEntry("session.db", prepared.conversation_identity_handle.value)
                    if prepared.conversation_identity_handle else None
                ),
                contact=(
                    ShardEntry("contact.db", prepared.display_identity_handle.value)
                    if prepared.display_identity_handle else None
                ),
                message_parts=entries,
            )
            provider = ShardedMessageProvider(
                ExplicitShardLocator(entries), identities=catalog.resolver()
            )
            result = consume(provider)
            if provider.diagnostics.unknown or provider.diagnostics.unavailable:
                # A part the request required could not be read. The read is
                # not the source's to claim, and no partial answer escapes.
                raise _DatabaseUnavailable()
            return result

    def _read(self, method: str, *args: object, fallback: bool):
        """One answer, and which source produced it.

        The flag is what keeps a fallback honest: only rows the database
        actually produced may carry a database reference, and only then may the
        answer be reported as the database's.
        """
        try:
            result = self._database(lambda provider: getattr(provider, method)(*args))
        except AcquisitionCleanupError:
            raise
        except _DatabaseUnavailable as error:
            if not fallback:
                raise
            return False, self._store(method, *args)
        except UnsupportedGeneration:
            # A changed generation is refused as unverified, never parsed on a
            # best-effort basis, and never surfaces as another source's answer.
            error = _DatabaseUnavailable("version_unverified")
            if not fallback:
                raise error from None
            return False, self._store(method, *args)
        except MessageSourceError:
            raise
        except Exception:
            error = _DatabaseUnavailable()
            if not fallback:
                raise error from None
            return False, self._store(method, *args)
        self.name = SOURCE_DATABASE
        return True, result

    def _store(self, method: str, *args: object):
        """The store answers in its own right: its ids, its coverage, its name.

        It is never dressed as the database, so a caller reading the envelope
        is told which reader actually answered.
        """
        self.name = SOURCE_VISUAL
        return getattr(self._visual_factory(), method)(*args)

    # -- the four questions ---------------------------------------------------

    def status(self) -> SourceStatus:
        try:
            self._read("list_conversations", 1, fallback=False)
        except MessageSourceError as error:
            return SourceStatus(
                source=SOURCE_DATABASE, ready=False, state=error.state,
                detail=error.detail,
            )
        return SourceStatus(source=SOURCE_DATABASE, ready=True, state="ready")

    def list_conversations(self, limit: int) -> ReadResult:
        from_database, result = self._read(
            "list_conversations", limit, fallback=True)
        return _tagged_conversations(result) if from_database else result

    def get_messages(
        self, conversation_id: int, limit: int, before_sequence: int | None = None
    ) -> ReadResult:
        try:
            local_id = database_conversation_id(conversation_id)
        except ValueError:
            raise MessageSourceError(
                REFERENCE_MISMATCH,
                "The conversation reference belongs to another source.",
            ) from None
        return _tagged_messages(
            self._read(
                "get_messages", local_id, limit, before_sequence, fallback=False
            )[1]
        )

    def get_recent_messages(self, since_observed_at: float, limit: int) -> ReadResult:
        from_database, result = self._read(
            "get_recent_messages", since_observed_at, limit, fallback=True)
        return _tagged_messages(result) if from_database else result
