"""Source-neutral contracts for preparing an optional local Reader source."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AcquisitionState(str, Enum):
    DISABLED = "disabled"
    NEEDS_BOOTSTRAP = "needs_bootstrap"
    READY = "ready"
    SOURCE_BUSY = "source_busy"
    SNAPSHOT_UNSTABLE = "snapshot_unstable"
    DECRYPT_FAILED = "decrypt_failed"
    SCHEMA_UNSUPPORTED = "schema_unsupported"
    VERSION_UNVERIFIED = "version_unverified"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class AcquisitionReadiness:
    state: AcquisitionState

    def __post_init__(self) -> None:
        if not isinstance(self.state, AcquisitionState):
            raise ValueError("acquisition state invalid")

    @property
    def database_mode_enabled(self) -> bool:
        return self.state is not AcquisitionState.DISABLED


@dataclass(frozen=True, slots=True)
class OpaqueHandle:
    value: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class PreparedSource:
    message_handles: tuple[OpaqueHandle, ...]
    conversation_identity_handle: OpaqueHandle | None = None
    display_identity_handle: OpaqueHandle | None = None

    def __post_init__(self) -> None:
        try:
            message_handles = tuple(self.message_handles)
        except TypeError:
            raise ValueError("prepared source invalid") from None
        if not message_handles or not all(
            isinstance(handle, OpaqueHandle) for handle in message_handles
        ):
            raise ValueError("prepared source invalid")
        if (self.conversation_identity_handle is not None
                and not isinstance(self.conversation_identity_handle, OpaqueHandle)):
            raise ValueError("prepared source invalid")
        if (self.display_identity_handle is not None
                and not isinstance(self.display_identity_handle, OpaqueHandle)):
            raise ValueError("prepared source invalid")
        object.__setattr__(self, "message_handles", message_handles)


@dataclass(frozen=True, slots=True)
class AcquisitionOutcome:
    readiness: AcquisitionReadiness
    prepared_source: PreparedSource | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.readiness, AcquisitionReadiness):
            raise ValueError("acquisition outcome contradiction")
        if (self.prepared_source is not None
                and not isinstance(self.prepared_source, PreparedSource)):
            raise ValueError("acquisition outcome contradiction")
        is_ready = self.readiness.state is AcquisitionState.READY
        if is_ready != (self.prepared_source is not None):
            raise ValueError("acquisition outcome contradiction")
