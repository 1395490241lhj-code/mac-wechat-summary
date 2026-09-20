"""Provider-internal orchestration around ``wechatdb``'s leaf parser.

This subpackage is where the candidate database provider's *composition*
lives: how a source made of several parts is inventoried, routed, read and
described. It is built around the existing leaf parser in
:mod:`wechatdb.parser`, which it calls and never replaces.

What it is not, stated once so nothing here has to be re-argued:

* **Not wired.** No product module imports it, and it is not registered as a
  Reader. Nothing that runs today reaches this package.
* **Not promoted.** It is exercised against synthetic fixtures only, and the
  existing product-to-``wechatdb`` guard already rejects an import of it at
  any depth. Promotion into the Reader path is a separate, explicit decision.
* **No acquisition.** It cannot find a database. It discovers no filesystem
  root, names no container, walks no directory and is handed every part it
  will ever see through an injected locator.
* **No decryption.** No key, passphrase, salt, cipher parameter, SQLCipher call
  or ``PRAGMA key``. A part it is given is already readable, or it is not.
* **No process access.** It reads no other process's memory, attaches no
  debugger and shells out to nothing.
* **Read-only, honestly.** The ``immutable`` open optimisation is forbidden
  because it silently ignores the write-ahead log; an unreadable log is an
  unavailable part, reported as such.

Importing :mod:`wechatdb` does not import this package. The leaf parser stays
usable without any of the orchestration here or the bridge boundary it will
construct results against.

Components land here one at a time and are re-exported from this module only
once they exist.
"""

from __future__ import annotations

from .discovery import (
    SHARD_KNOWN,
    SHARD_READABLE,
    SHARD_STATES,
    SHARD_UNAVAILABLE,
    SHARD_UNKNOWN,
    ExplicitShardLocator,
    ReadOnlySqliteOpener,
    ShardDiscovery,
    ShardEntry,
    ShardFacts,
    ShardLocator,
    ShardOpener,
    shard_key,
)
from .compatibility import (
    SUPPORTED_CONVERSATION_COLUMNS,
    UnsupportedGeneration,
    require_supported_surface,
)
from .routing import (
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
from .identity import (
    NAME_CONTACT_NICKNAME,
    NAME_CONTACT_REMARK,
    NAME_PRECEDENCE,
    NAME_ROOM_MEMBER,
    IdentityResolver,
    NameCandidate,
    ResolvedIdentities,
)
from .identity_catalog import IdentityCatalog
from .provider import ShardedMessageProvider
from .result import Contribution, ProviderDiagnostics, ProviderResult

__all__ = [
    "SUPPORTED_CONVERSATION_COLUMNS",
    "UnsupportedGeneration",
    "require_supported_surface",
    "Contribution",
    "ProviderDiagnostics",
    "ProviderResult",
    "ShardedMessageProvider",
    "NAME_CONTACT_NICKNAME",
    "NAME_CONTACT_REMARK",
    "NAME_PRECEDENCE",
    "NAME_ROOM_MEMBER",
    "IdentityResolver",
    "IdentityCatalog",
    "NameCandidate",
    "ResolvedIdentities",
    "EXCLUDED_CONVERSATION_ABSENT",
    "EXCLUDED_NOT_READABLE",
    "EXCLUDED_OUT_OF_WINDOW",
    "EXCLUSION_CAUSES",
    "STOP_EXHAUSTED",
    "STOP_KINDS",
    "STOP_SAFE",
    "STOP_UNSAFE",
    "RoutePlan",
    "ShardRouter",
    "SHARD_KNOWN",
    "SHARD_READABLE",
    "SHARD_STATES",
    "SHARD_UNAVAILABLE",
    "SHARD_UNKNOWN",
    "ExplicitShardLocator",
    "ReadOnlySqliteOpener",
    "ShardDiscovery",
    "ShardEntry",
    "ShardFacts",
    "ShardLocator",
    "ShardOpener",
    "shard_key",
]
