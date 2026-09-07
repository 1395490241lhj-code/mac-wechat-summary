"""The one place that knows where the app-owned memory store lives.

The store is the *app's*, not the operator's: there is a single canonical
location under the app's own Application Support directory, beside the message
store the app already writes, and neither the app nor the worker nor a test
may spell it independently. Duplicated path literals are how two components
end up pointing at two files and nobody notices until a sync appears to do
nothing.

```
~/Library/Application Support/WeChatCompanion/memory.sqlite
```

Two rules make this safe to hand around.

**The directory is not created here.** Deriving a path is not permission to
occupy it. Only :func:`prepare_store_directory` creates anything, and only a
caller that has already passed the consent gate calls it -- so a machine whose
user never consented has no directory, not an empty one.

**The absolute path is not a public value.** It contains the user's home
directory. :func:`relative_store_path` is what may be shown, logged, or put on
a wire; the absolute form stays inside the process that needs to open the file.
Tests and the operator CLI still inject an explicit path through the existing
``WECHAT_COMPANION_MEMORY_DB_PATH`` seam, which this module does not replace
and does not read.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The app's own directory inside Application Support. The same name the
#: Swift ``MessageStore.applicationSupport`` uses; the two must agree.
APP_DIRECTORY_NAME: str = "WeChatCompanion"

#: The memory store's file name, beside ``messages.sqlite``.
STORE_FILE_NAME: str = "memory.sqlite"

#: The app's existing message store, in the same owned directory. Named here
#: so the worker can be told which store to read without a second literal;
#: this module does not open it and does not create it.
MESSAGE_STORE_FILE_NAME: str = "messages.sqlite"

#: The path from the user's home directory to the store. Safe to display: it
#: names the location without naming the user.
RELATIVE_STORE_COMPONENTS: tuple[str, ...] = (
    "Library", "Application Support", APP_DIRECTORY_NAME, STORE_FILE_NAME,
)


def relative_store_path() -> str:
    """``Library/Application Support/WeChatCompanion/memory.sqlite``."""
    return "/".join(RELATIVE_STORE_COMPONENTS)


def canonical_store_path(home: str | os.PathLike[str] | None = None) -> Path:
    """The absolute store path. Creates nothing.

    ``home`` exists so a test can point the derivation at a temporary
    directory without touching the real one; production passes nothing.
    """
    base = Path(home).expanduser() if home is not None else Path.home()
    return base.joinpath(*RELATIVE_STORE_COMPONENTS)


def canonical_message_store_path(home: str | os.PathLike[str] | None = None) -> Path:
    """The app's message store, beside the memory store. Creates nothing."""
    base = Path(home).expanduser() if home is not None else Path.home()
    return base.joinpath(*RELATIVE_STORE_COMPONENTS[:-1], MESSAGE_STORE_FILE_NAME)


def prepare_store_directory(path: str | os.PathLike[str]) -> Path:
    """Creates the store's parent directory, owner-only. Call after consent.

    Returns the path it was given, so a caller can use it in one expression.
    The directory is ``0700``; the store file itself is made ``0600`` by
    :class:`memory_store.MemoryStore` when it is opened.
    """
    target = Path(path)
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return target


__all__ = [
    "APP_DIRECTORY_NAME",
    "MESSAGE_STORE_FILE_NAME",
    "canonical_message_store_path",
    "RELATIVE_STORE_COMPONENTS",
    "STORE_FILE_NAME",
    "canonical_store_path",
    "prepare_store_directory",
    "relative_store_path",
]
