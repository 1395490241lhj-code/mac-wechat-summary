"""Tests for explicit message-source activation on the Claude backend.

Nothing here runs Claude Code, a bridge, a reader, or a database. Every test
inspects the configuration a run *would* use, or the argument parser's refusal
to build one. No real WeChat data, container, or credential is involved.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from agent_runner import ShadowError
from runners.claude import (
    KNOWN_SOURCES,
    MESSAGE_SOURCE_ENV,
    READER_BIN_ENV,
    READER_CONFIG_ENV,
    READER_TIMEOUT_ENV,
    SOURCE_DATABASE,
    SOURCE_VISUAL,
    ClaudeConfig,
)
from wechat_shadow_run import build_parser, make_runner

STORE_ENV = {"WECHAT_COMPANION_ALLOW_AGENT_READ": "1"}


def make_config(tmp_path: Path, **kwargs) -> ClaudeConfig:
    skill = tmp_path / "SKILL.md"
    skill.write_bytes(b"policy")
    return ClaudeConfig(
        claude_bin=tmp_path / "claude", python=tmp_path / "python",
        bridge=tmp_path / "bridge.py", db_path=tmp_path / "store.sqlite",
        skill=skill, isolated_home=tmp_path / "isolated", **kwargs,
    )


def server_env(cfg: ClaudeConfig) -> dict:
    return cfg.mcp_config_document()["mcpServers"]["wechat_companion"]["env"]


# --- Default: the visual store, by saying nothing ----------------------------

def test_a_run_that_asks_for_nothing_carries_no_source_variables(tmp_path):
    cfg = make_config(tmp_path)

    assert cfg.message_source is None
    assert cfg.source_env() == {}
    # The server environment is exactly the two the bridge has always had.
    assert server_env(cfg) == {
        "WECHAT_COMPANION_ALLOW_AGENT_READ": "1",
        "WECHAT_COMPANION_DB_PATH": str(cfg.db_path),
    }


def test_the_default_run_mentions_no_reader_anywhere(tmp_path):
    cfg = make_config(tmp_path)
    document = json.dumps(cfg.mcp_config_document())

    for name in (MESSAGE_SOURCE_ENV, READER_BIN_ENV, READER_CONFIG_ENV,
                 READER_TIMEOUT_ENV):
        assert name not in document


def test_source_variables_never_enter_the_agents_own_environment(tmp_path):
    """Activation belongs to the bridge process, not to Claude Code."""
    cfg = make_config(tmp_path, message_source=SOURCE_DATABASE,
                      reader_bin=tmp_path / "reader")
    child = cfg.child_env("http://127.0.0.1:18823")

    for name in (MESSAGE_SOURCE_ENV, READER_BIN_ENV):
        assert name not in child


# --- Explicit selection ------------------------------------------------------

def test_selecting_the_visual_source_explicitly_pins_it(tmp_path):
    """Naming the default is not the same as saying nothing."""
    cfg = make_config(tmp_path, message_source=SOURCE_VISUAL)

    assert cfg.source_env() == {MESSAGE_SOURCE_ENV: SOURCE_VISUAL}
    assert READER_BIN_ENV not in server_env(cfg)


def test_selecting_the_database_source_passes_the_injected_path(tmp_path):
    reader = tmp_path / "an-injected-reader"
    cfg = make_config(tmp_path, message_source=SOURCE_DATABASE, reader_bin=reader)

    assert cfg.source_env() == {
        MESSAGE_SOURCE_ENV: SOURCE_DATABASE,
        READER_BIN_ENV: str(reader),
    }
    assert server_env(cfg)["WECHAT_COMPANION_ALLOW_AGENT_READ"] == "1"


def test_optional_reader_settings_are_passed_only_when_supplied(tmp_path):
    reader = tmp_path / "reader"
    bare = make_config(tmp_path, message_source=SOURCE_DATABASE, reader_bin=reader)
    full = make_config(tmp_path, message_source=SOURCE_DATABASE, reader_bin=reader,
                       reader_config=tmp_path / "reader.json", reader_timeout=12.5)

    assert READER_CONFIG_ENV not in bare.source_env()
    assert READER_TIMEOUT_ENV not in bare.source_env()
    assert full.source_env()[READER_CONFIG_ENV] == str(tmp_path / "reader.json")
    assert full.source_env()[READER_TIMEOUT_ENV] == "12.5"


def test_selection_is_case_and_whitespace_tolerant(tmp_path):
    cfg = make_config(tmp_path, message_source="  DataBase  ",
                      reader_bin=tmp_path / "reader")

    assert cfg.source_env()[MESSAGE_SOURCE_ENV] == SOURCE_DATABASE


# --- Failing closed ----------------------------------------------------------

def test_the_database_source_without_a_reader_refuses_to_build(tmp_path):
    """Omitting the variables instead would silently read the visual store."""
    cfg = make_config(tmp_path, message_source=SOURCE_DATABASE)

    with pytest.raises(ShadowError) as raised:
        cfg.source_env()
    assert "never searched" in str(raised.value)

    with pytest.raises(ShadowError):
        cfg.mcp_config_document()


def test_an_unknown_source_refuses_to_build(tmp_path):
    cfg = make_config(tmp_path, message_source="somewhere-else")

    with pytest.raises(ShadowError) as raised:
        cfg.source_env()
    assert "unknown message source" in str(raised.value)


def test_the_known_sources_are_only_the_two(tmp_path):
    assert KNOWN_SOURCES == {SOURCE_VISUAL, SOURCE_DATABASE}


# --- The runner and the bridge agree on the variable names -------------------

def test_the_runner_and_the_bridge_name_the_same_variables():
    """Two copies of a string are a drift risk; this is the guard.

    The bridge module is loaded from its file so that the shadow suite keeps
    no import-time dependency on the bridge or its MCP requirement.
    """
    import sys

    path = Path(__file__).resolve().parents[2] / "bridge" / "message_source.py"
    name = "_boundary_for_test"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered while executing: its dataclasses resolve their annotations
    # through sys.modules, and removed again so the suite leaves no import.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)

    assert MESSAGE_SOURCE_ENV == module.MESSAGE_SOURCE_ENV
    assert READER_BIN_ENV == module.READER_BIN_ENV
    assert READER_CONFIG_ENV == module.READER_CONFIG_ENV
    assert READER_TIMEOUT_ENV == module.READER_TIMEOUT_ENV
    assert SOURCE_VISUAL == module.SOURCE_VISUAL
    assert SOURCE_DATABASE == module.SOURCE_DATABASE
    assert KNOWN_SOURCES == module.SOURCE_NAMES
    assert module.ACTIVATION_ENV_NAMES == {
        MESSAGE_SOURCE_ENV, READER_BIN_ENV, READER_CONFIG_ENV, READER_TIMEOUT_ENV}


# --- The command line --------------------------------------------------------

def base_argv(tmp_path: Path) -> list[str]:
    return [
        "--agent-backend", "claude",
        "--isolated-home", str(tmp_path / "isolated"),
        "--claude-bin", str(tmp_path / "claude"),
        "--python", str(tmp_path / "python"),
        "--bridge", str(tmp_path / "bridge.py"),
        "--db-path", str(tmp_path / "store.sqlite"),
        "--skill", str(tmp_path / "SKILL.md"),
    ]


def build(tmp_path: Path, extra: list[str]):
    parser = build_parser()
    return make_runner(parser.parse_args(base_argv(tmp_path) + extra), parser,
                       environ={})


def test_the_command_line_defaults_to_no_source_selection(tmp_path):
    runner = build(tmp_path, [])

    assert runner.cfg.message_source is None
    assert runner.cfg.source_env() == {}


def test_the_command_line_can_request_the_database_source(tmp_path):
    reader = tmp_path / "reader"
    runner = build(tmp_path, ["--message-source", "database",
                              "--reader-bin", str(reader),
                              "--reader-timeout", "20"])

    assert runner.cfg.source_env() == {
        MESSAGE_SOURCE_ENV: SOURCE_DATABASE,
        READER_BIN_ENV: str(reader),
        READER_TIMEOUT_ENV: "20.0",
    }


def test_requesting_the_database_source_without_a_reader_is_rejected(tmp_path):
    with pytest.raises(SystemExit) as raised:
        build(tmp_path, ["--message-source", "database"])
    assert raised.value.code == 2


def test_a_reader_without_a_selection_is_rejected(tmp_path):
    """Configuring a reader but not selecting it would read the visual store."""
    with pytest.raises(SystemExit) as raised:
        build(tmp_path, ["--reader-bin", str(tmp_path / "reader")])
    assert raised.value.code == 2


def test_an_unknown_source_is_rejected_by_the_parser(tmp_path):
    with pytest.raises(SystemExit):
        build(tmp_path, ["--message-source", "elsewhere"])


def test_source_flags_are_refused_on_the_other_backend(tmp_path):
    parser = build_parser()
    argv = [
        "--agent-backend", "hermes",
        "--isolated-home", str(tmp_path / "isolated"),
        "--hermes-entry", str(tmp_path / "hermes"),
        "--python", str(tmp_path / "python"),
        "--hermes-home", str(tmp_path / "hhome"),
        "--project-dir", str(tmp_path),
        "--message-source", "database",
        "--reader-bin", str(tmp_path / "reader"),
    ]
    with pytest.raises(SystemExit) as raised:
        make_runner(parser.parse_args(argv), parser, environ={})
    assert raised.value.code == 2
