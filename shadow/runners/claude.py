#!/usr/bin/env python3
"""Claude Code headless backend for the shadow digest — synthetic-only.

Runs ``claude -p`` (Claude Code 2.1.x) with the narrowest configuration the
installed CLI supports:

* ``--tools ""`` — no built-in tool (no Bash, no file read/write/edit, no web).
* ``--strict-mcp-config --mcp-config <isolated json>`` — exactly one MCP
  server, the read-only ``wechat_companion`` bridge; nothing from any user or
  project MCP configuration.
* ``--permission-mode dontAsk`` — no interactive prompt can ever appear; a tool
  that is not pre-allowed is denied, never asked about.
* ``--allowedTools`` — only the bridge's tools are pre-allowed.
* ``--no-session-persistence`` — the CLI is told not to save the session.
* ``--setting-sources ""`` and an empty working directory — no user, project or
  local settings, no ``CLAUDE.md``, no hooks, no plugins are discovered.
  ``--settings '{"autoMemoryEnabled":false}'`` turns auto-memory off explicitly
  and ``--disable-slash-commands`` disables skills discovery.
* **No ``--bare``.** Bare mode would restrict auth to ``ANTHROPIC_API_KEY``; the
  isolation above is enforced flag by flag instead, so a subscription OAuth
  token (``CLAUDE_CODE_OAUTH_TOKEN`` from ``claude setup-token``) works.
  Credentials enter only through the CLI's ``--pass-env NAME`` allowlist: read
  from the parent environment, copied into the child environment, never on
  argv, never logged, never persisted.
* ``--system-prompt <SKILL.md bytes>`` — the digest policy is the skill file,
  read verbatim at launch and passed as the whole system prompt. This build
  has no ``--system-prompt-file`` flag, so the bytes travel on argv; nothing is
  copied to disk, reformatted, or rewritten.

**None of that is trusted.** Claude Code prints no tool selection, so the
boundary is verified where it is real: on the wire. The runner starts a
``ToolBoundaryProxy`` in-process and points the CLI at it through
``ANTHROPIC_BASE_URL``. Before the digest, a *probe* turn is sent with the
proxy in ``probe`` mode: the first tools-bearing request is recorded and
rejected without being forwarded, so the actual ``tools[]`` array is observed
with zero provider traffic and no message read. Only if it equals the expected
set does the digest turn run, with the proxy in ``enforce`` mode for every
subsequent request.

**Persistence.** Everything the CLI may write is confined to one dedicated
directory: ``HOME``, ``CLAUDE_CONFIG_DIR`` and the working directory all
resolve inside ``isolated_home``. "Clean" for this backend means that
directory contains no files at all; preflight and post-run cleanup remove
its contents and prove that. Whether Claude Code writes chat content anywhere
inside it despite ``--no-session-persistence`` is the subject of the H6
canary audit, not an assumption made here.

**Memory MCP (M2.1).** Off by default. With ``memory_enabled`` the isolated
MCP config names a second, separate read-only server over the memory store
(``memory/wechat_memory_mcp.py``) and the expected wire set becomes exactly
eight: the four bridge tools plus ``memory_search``, ``memory_timeline``,
``memory_context``, ``memory_recent``. The request is validated before the
run -- server present, database path given, the app's consent state
allowing, the store readable at a supported version -- and refuses to start
otherwise. The memory database path reaches the memory server's environment
only.

Not enabled, by construction: any built-in tool, cron, unattended execution,
session resume, plugins, hooks, Claude Code's own memory, skills discovery.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from agent_runner import DigestResult, PROMPT, ShadowError, check_tool_boundary
from tool_boundary_proxy import ToolBoundaryProxy

#: The bridge's own tool names, as it declares them. The server key and these
#: names are the only inputs to the expected wire set; the join convention is
#: Claude Code's documented ``mcp__<server>__<tool>`` and is *verified*, not
#: assumed — the proxy compares against what is actually sent.
BRIDGE_SERVER = "wechat_companion"
BRIDGE_TOOLS = ("status", "list_conversations", "get_messages", "get_recent_messages")

#: The bridge's message-source activation variables, as it declares them in
#: ``bridge/message_source.py``. They are repeated here rather than imported so
#: that this runner keeps no import-time dependency on the bridge it launches
#: as a subprocess; a test compares the two definitions and fails if they drift.
SOURCE_VISUAL = "visual"
SOURCE_DATABASE = "database"
KNOWN_SOURCES = frozenset({SOURCE_VISUAL, SOURCE_DATABASE})
MESSAGE_SOURCE_ENV = "WECHAT_COMPANION_MESSAGE_SOURCE"
READER_BIN_ENV = "WECHAT_COMPANION_READER_BIN"
READER_CONFIG_ENV = "WECHAT_COMPANION_READER_CONFIG"
READER_TIMEOUT_ENV = "WECHAT_COMPANION_READER_TIMEOUT"

#: The memory server's own names, as ``memory/wechat_memory_mcp.py`` declares
#: them, and its activation variables as ``memory/memory_consent.py`` declares
#: them. Repeated here for the same reason as the bridge's: the runner keeps no
#: import-time dependency on what it launches, and a test compares the copies.
MEMORY_SERVER = "wechat_memory"
MEMORY_TOOLS = ("memory_search", "memory_timeline", "memory_context", "memory_recent")
MEMORY_ENABLED_ENV = "WECHAT_COMPANION_MEMORY_ENABLED"
MEMORY_DB_PATH_ENV = "WECHAT_COMPANION_MEMORY_DB_PATH"

DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_SKILL = Path(".hermes/skills/wechat-digest/SKILL.md")
PROBE_PROMPT = "boundary-probe"

#: Explicit settings passed inline (not a settings *source*): auto-memory off.
INLINE_SETTINGS = json.dumps({"autoMemoryEnabled": False}, separators=(",", ":"))


def claude_wire_name(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


CLAUDE_EXPECTED_TOOLS = frozenset(claude_wire_name(BRIDGE_SERVER, t) for t in BRIDGE_TOOLS)
CLAUDE_MEMORY_TOOLS = frozenset(claude_wire_name(MEMORY_SERVER, t) for t in MEMORY_TOOLS)
#: The whole surface with memory explicitly enabled: the four bridge tools
#: plus the four memory tools, and nothing else. Exactly eight.
CLAUDE_EXPECTED_TOOLS_WITH_MEMORY = CLAUDE_EXPECTED_TOOLS | CLAUDE_MEMORY_TOOLS


@dataclass
class ClaudeConfig:
    claude_bin: Path        # the `claude` executable to run
    python: Path            # interpreter that has the bridge's `mcp` dependency
    bridge: Path            # bridge/wechat_companion_mcp.py
    db_path: Path           # the SQLite store the bridge may read (synthetic only, for now)
    skill: Path             # SKILL.md — the single source of digest policy
    isolated_home: Path     # HOME + CLAUDE_CONFIG_DIR + cwd for the child; owned by the run
    model: str = DEFAULT_MODEL
    proxy_port: int = 8823
    extra_env: dict = field(default_factory=dict)
    timeout: int = 900

    # --- Message-source activation, off unless asked for ---------------------
    #
    # ``None`` means the run says nothing about sources, so the bridge uses the
    # visual store it has always used. Any other value is a deliberate request
    # and is validated before the run starts, because the one outcome worse
    # than refusing to run is running against a different source than the
    # operator asked for.
    message_source: str | None = None
    reader_bin: Path | None = None       # injected; never searched for
    reader_config: Path | None = None
    reader_timeout: float | None = None

    # --- Memory MCP, off unless asked for ------------------------------------
    #
    # A second, separate read-only server over the memory store. Off by
    # default, and when off the run is exactly the four-tool run it has always
    # been: no server, no variable, no expected tool. When on, everything it
    # needs is validated before the run starts and the expected surface becomes
    # exactly eight. A request that cannot be honoured refuses to start rather
    # than quietly running with the four bridge tools alone.
    memory_enabled: bool = False
    memory_db_path: Path | None = None   # reaches the memory server env only
    memory_server: Path | None = None    # memory/wechat_memory_mcp.py; injected
    #: Reads the app's consent state for the pre-run check. Injectable so tests
    #: never touch the real preference domain; the server itself always reads
    #: the real one.
    memory_consent_reader: Callable | None = None

    @property
    def config_dir(self) -> Path:
        return self.isolated_home / ".claude"

    @property
    def workdir(self) -> Path:
        return self.isolated_home / "cwd"

    @property
    def mcp_config(self) -> Path:
        return self.isolated_home / "mcp.json"

    @property
    def proxy_state(self) -> Path:
        return self.isolated_home / "tool_boundary.json"

    def child_env(self, proxy_url: str) -> dict:
        """A minimal, explicit environment, built from scratch.

        Nothing from the operator's shell — credentials, base-URL overrides,
        Claude Code state, the parent Claude Code session — can reach the child.
        """
        env = {
            "HOME": str(self.isolated_home),
            "CLAUDE_CONFIG_DIR": str(self.config_dir),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TERM": "dumb",
            "ANTHROPIC_BASE_URL": proxy_url,
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
            "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
            "CLAUDE_CODE_DISABLE_WORKFLOWS": "1",
        }
        env.update(self.extra_env)
        return env

    def source_env(self) -> dict:
        """Activation variables for a deliberately selected source.

        Empty unless a source was requested, which is what keeps the visual
        store the default: a run that says nothing adds nothing, and the bridge
        falls back to nothing because there is nothing to fall back from.

        A request that cannot be honoured raises here, before the run starts.
        Omitting the variables instead would launch a run that reads the visual
        store while the operator believes they selected another source, and a
        silent substitution of one reader's coverage for another's is the exact
        failure the source boundary exists to prevent.
        """
        if self.message_source is None:
            return {}
        selected = self.message_source.strip().lower()
        if selected not in KNOWN_SOURCES:
            raise ShadowError(
                f"unknown message source {selected!r}; "
                f"expected one of {sorted(KNOWN_SOURCES)}"
            )
        if selected == SOURCE_VISUAL:
            # Naming the default explicitly is allowed and is not the same as
            # saying nothing: it pins the source against a changed default.
            return {MESSAGE_SOURCE_ENV: SOURCE_VISUAL}
        if self.reader_bin is None:
            raise ShadowError(
                "the database message source requires an explicit reader "
                "executable; it is never searched for or discovered"
            )
        env = {
            MESSAGE_SOURCE_ENV: SOURCE_DATABASE,
            READER_BIN_ENV: str(self.reader_bin),
        }
        if self.reader_config is not None:
            env[READER_CONFIG_ENV] = str(self.reader_config)
        if self.reader_timeout is not None:
            env[READER_TIMEOUT_ENV] = str(self.reader_timeout)
        return env

    @property
    def expected_tools(self) -> frozenset[str]:
        """Exactly four, or exactly eight. Nothing in between."""
        return CLAUDE_EXPECTED_TOOLS_WITH_MEMORY if self.memory_enabled else CLAUDE_EXPECTED_TOOLS

    def memory_env(self) -> dict:
        """Activation variables for the memory server, or nothing at all.

        Validates the whole request first: the server file, the database
        path, the app's consent state, and that the store can be opened
        read-only at a supported schema version. Any failure raises here,
        before the run starts. Continuing with the bridge alone would let the
        operator believe memory was in the run when it was not, and a digest
        that silently lacks the memory it was asked to use is the failure this
        gate exists to prevent.
        """
        if not self.memory_enabled:
            if self.memory_db_path is not None or self.memory_server is not None:
                raise ShadowError("--memory-db-path and --memory-server require --memory")
            return {}
        if self.memory_server is None or not self.memory_server.is_file():
            raise ShadowError("memory: the memory server script was not supplied or does not exist")
        if self.memory_db_path is None:
            raise ShadowError("memory: an explicit memory database path is required")
        env = {MEMORY_ENABLED_ENV: "1", MEMORY_DB_PATH_ENV: str(self.memory_db_path)}
        self._check_memory_available(env)
        return env

    def _check_memory_available(self, env: dict) -> None:
        """Consent and readability, checked with the memory layer's own gate."""
        import importlib.util

        memory_dir = self.memory_server.resolve().parent
        loaded = {}
        for name in ("memory_consent", "memory_store"):
            spec = importlib.util.spec_from_file_location(name, memory_dir / f"{name}.py")
            if spec is None or spec.loader is None:
                raise ShadowError("memory: the memory layer could not be loaded")
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            try:
                spec.loader.exec_module(module)
            except Exception as exc:  # noqa: BLE001
                sys.modules.pop(name, None)
                raise ShadowError(f"memory: the memory layer could not be loaded "
                                  f"({exc.__class__.__name__})") from exc
            loaded[name] = module
        consent, store = loaded["memory_consent"], loaded["memory_store"]
        reader = self.memory_consent_reader or consent.read_app_consent_state_macos
        decision = consent.resolve_consent(env, reader)
        if not decision.allowed:
            raise ShadowError(f"memory: refused before the run ({decision.state})")
        try:
            store.MemoryStore.open_read_only(decision).close()
        except store.MemoryStoreError as exc:
            raise ShadowError(f"memory: the store cannot be read ({exc.state})") from exc

    def mcp_config_document(self) -> dict:
        """The bridge, and the memory server only when explicitly enabled.

        Each server's environment carries its own opt-ins and nothing of the
        other's: the memory database path is visible to the memory server
        process alone, never to the bridge and never to Claude Code itself.
        """
        servers = {BRIDGE_SERVER: {
            "type": "stdio",
            "command": str(self.python),
            "args": [str(self.bridge)],
            "env": {
                "WECHAT_COMPANION_ALLOW_AGENT_READ": "1",
                "WECHAT_COMPANION_DB_PATH": str(self.db_path),
                **self.source_env(),
            },
        }}
        memory_env = self.memory_env()
        if memory_env:
            servers[MEMORY_SERVER] = {
                "type": "stdio",
                "command": str(self.python),
                "args": [str(self.memory_server)],
                "env": memory_env,
            }
        return {"mcpServers": servers}


class ClaudeRunner:
    """``AgentRunner`` over Claude Code headless.

    ``run`` defaults to ``subprocess.run`` and ``proxy_factory`` to
    ``ToolBoundaryProxy``; both are injectable so the tests can pin argv,
    environment, ordering and fail-closed behaviour without a runtime.
    """

    backend = "claude"

    @property
    def expected_tools(self) -> frozenset[str]:
        return self.cfg.expected_tools

    def __init__(self, cfg: ClaudeConfig, *, run: Callable = subprocess.run,
                 proxy_factory: Callable = ToolBoundaryProxy, err=sys.stderr) -> None:
        self.cfg = cfg
        self._run = run
        self._proxy_factory = proxy_factory
        self._err = err
        self._proxy: ToolBoundaryProxy | None = None

    # --- launch --------------------------------------------------------------

    def skill_text(self) -> str:
        """SKILL.md, verbatim. Never modified, never copied to disk."""
        return self.cfg.skill.read_text(encoding="utf-8")

    def argv(self, prompt: str) -> list[str]:
        cfg = self.cfg
        allowed = ",".join(sorted(self.expected_tools))
        return [
            str(cfg.claude_bin), "-p",
            "--tools", "",
            "--strict-mcp-config", "--mcp-config", str(cfg.mcp_config),
            "--allowedTools", allowed,
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
            "--setting-sources", "",
            "--settings", INLINE_SETTINGS,
            "--disable-slash-commands",
            "--output-format", "json",
            "--model", cfg.model,
            "--system-prompt", self.skill_text(),
            prompt,
        ]

    def _prepare(self) -> None:
        cfg = self.cfg
        cfg.config_dir.mkdir(parents=True, exist_ok=True)
        cfg.workdir.mkdir(parents=True, exist_ok=True)
        cfg.mcp_config.write_text(json.dumps(cfg.mcp_config_document(), indent=1),
                                  encoding="utf-8")

    def _claude(self, prompt: str) -> subprocess.CompletedProcess:
        assert self._proxy is not None, "proxy must be running"
        cfg = self.cfg
        return self._run(
            self.argv(prompt), cwd=str(cfg.workdir),
            env=cfg.child_env(self._proxy.url),
            stdin=subprocess.DEVNULL,  # the CLI otherwise waits 3 s for piped stdin
            capture_output=True, text=True, timeout=cfg.timeout,
        )

    # --- persistence ---------------------------------------------------------

    def residue(self) -> list[Path]:
        """Every file under the isolated home. Names only; contents never read."""
        home = self.cfg.isolated_home
        return sorted(p for p in home.rglob("*") if p.is_file()) if home.is_dir() else []

    def purge(self) -> None:
        """Remove everything the run owns. The directory itself stays."""
        if self._proxy is not None:
            self._proxy.stop()
            self._proxy = None
        home = self.cfg.isolated_home
        if home.is_dir():
            for child in home.iterdir():
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)

    def assert_clean(self, stage: str) -> None:
        left = self.residue()
        if left:
            raise ShadowError(f"{stage}: state is not clean — {len(left)} file(s) remain "
                              f"under the isolated home")

    # --- AgentRunner ---------------------------------------------------------

    def preflight(self) -> None:
        self.cfg.isolated_home.mkdir(parents=True, exist_ok=True)
        stale = self.residue()
        if stale:
            print(f"preflight: recovering {len(stale)} stale file(s)", file=self._err)
            self.purge()
        self.assert_clean("preflight")
        if not self.cfg.skill.is_file():
            raise ShadowError("preflight: SKILL.md not found at the configured path")
        print("preflight: clean state verified", file=self._err)

    def assert_tool_boundary(self) -> set[str]:
        """Observe the actual ``tools[]`` on the wire, with nothing forwarded."""
        cfg = self.cfg
        self._prepare()
        try:
            self._proxy = self._proxy_factory(
                self.expected_tools, port=cfg.proxy_port,
                state_path=str(cfg.proxy_state), mode="probe",
            ).start()
        except OSError as exc:
            raise ShadowError(f"tool boundary: proxy could not listen on port "
                              f"{cfg.proxy_port} ({exc.__class__.__name__})") from exc
        # The probe turn never reaches the provider: the proxy records the
        # request's tool names and rejects it. The CLI exits non-zero, which is
        # the expected outcome here.
        self._claude(PROBE_PROMPT)
        state = self._proxy.state
        if state.tools_bearing < 1:
            raise ShadowError("tool boundary: Claude never sent a tools-bearing request")
        tools = set(state.observed)
        check_tool_boundary(tools, self.expected_tools)
        # Reset the tally so the digest turn is judged on its own requests,
        # then let matching requests through.
        self._proxy.state.reset()
        self._proxy.mode = "enforce"
        return tools

    #: Envelope keys safe to keep. ``result`` is kept only when it is an error
    #: string, truncated, never a digest.
    ENVELOPE_KEYS = ("type", "subtype", "is_error", "terminal_reason", "api_error_status",
                     "num_turns", "duration_api_ms", "stop_reason", "total_cost_usd")

    def digest(self) -> DigestResult:
        try:
            out = self._claude(PROMPT)
        finally:
            # The digest turn is the last spawn; drop credential references now.
            for name in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"):
                self.cfg.extra_env.pop(name, None)
        text, run_id, rc = "", None, out.returncode
        diag: dict = {"returncode": out.returncode, "stdout_bytes": len(out.stdout or ""),
                      "stderr_bytes": len(out.stderr or "")}
        try:
            payload = json.loads(out.stdout or "{}")
            diag["stdout_json"] = True
            run_id = payload.get("session_id")
            diag.update({k: payload[k] for k in self.ENVELOPE_KEYS if k in payload})
            diag["permission_denials"] = len(payload.get("permission_denials") or [])
            if payload.get("is_error"):
                rc = rc or 1
                diag["error_text"] = str(payload.get("result") or "")[:200]
            elif rc == 0:
                text = (payload.get("result") or "").strip()
        except json.JSONDecodeError:
            diag["stdout_json"] = False
            rc = rc or 1
        if rc != 0:
            diag["stderr_head"] = (out.stderr or "")[:300]
        assert self._proxy is not None
        state = self._proxy.state
        diag["proxy"] = {"requests": state.requests, "tools_bearing": state.tools_bearing,
                         "violations": state.violations,
                         "upstream_status": list(getattr(state, "upstream_status", [])),
                         "forward_errors": list(getattr(state, "forward_errors", []))}
        if state.violations:
            raise ShadowError(f"tool boundary: {state.violations} violating request(s) "
                              f"aborted on the wire during the digest")
        if state.tools_bearing < 1:
            raise ShadowError("tool boundary: the digest turn carried no tools")
        return DigestResult(text=text, run_id=run_id, returncode=rc, diagnostics=diag)

    def cleanup(self, run_id: str | None) -> None:
        self.purge()
        self.assert_clean("post-run")
