"""One ``AgentRunner`` implementation per agent runtime.

* ``hermes`` — the original backend (Hermes Agent CLI). Behaviour-identical to
  the pre-H6 monolithic runner; kept as an optional backend.
* ``claude`` — Claude Code headless (``claude -p``). Synthetic-only until its
  own gates are sealed.

Selecting a backend is a CLI flag on ``shadow/wechat_shadow_run.py``. Nothing
in this package reads the digest policy; every backend consumes the same
``SKILL.md`` unmodified.
"""

from __future__ import annotations

BACKENDS = ("hermes", "claude")
