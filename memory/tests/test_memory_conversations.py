"""M2.2b conversation discovery: matching rule, ambiguity, logical grouping,
the public tool, and the live server."""

from __future__ import annotations

import asyncio
import json
import os
import plistlib
import sys
from pathlib import Path

import pytest

import memory_consent as consent
import memory_identity as identity
import wechat_memory_mcp as server
from conftest import app_state, conversation, database_message, granted, visual_message
from memory_ingest import MemoryIngestor
from memory_query import (
    MATCH_ALL,
    MATCH_CONTAINS,
    MATCH_EXACT,
    ConversationDiscoveryResult,
    MemoryQueryService,
    normalize_name,
)
from memory_store import (
    COVERAGE_COMPLETE,
    LINK_KIND_CONVERSATION,
    LINK_OPERATOR,
    CoverageRecord,
    MemoryStore,
    MemoryStoreError,
)
from message_source import SOURCE_DATABASE, SOURCE_VISUAL

BASE = 1_700_000_000.0


def cid(source, n):
    return identity.conversation_canonical_id(source, str(n))


def call(tool, **kwargs):
    return getattr(tool, "fn", tool)(**kwargs)


def populate(store):
    """Invented: a unique title, two conversations sharing a title, one linked pair."""
    ingestor = MemoryIngestor(store)
    ingestor.ingest(
        SOURCE_VISUAL,
        [visual_message(1, 7, "发布日期定在 11 月 3 日", observed_at=BASE + 10),
         visual_message(2, 9, "发布日期还没定", observed_at=BASE + 20),
         visual_message(3, 11, "评审定在周四十点", observed_at=BASE + 30),
         visual_message(4, 13, "报销单周五前交", observed_at=BASE + 40)],
        conversations=[conversation(7, "产品群", last=BASE + 10), conversation(9, "产品群", last=BASE + 20),
                       conversation(11, "设计评审群", last=BASE + 30), conversation(13, "财务对接", last=BASE + 40)],
        coverage=[CoverageRecord(source=SOURCE_VISUAL, status=COVERAGE_COMPLETE)], now=1.0,
    )
    ingestor.ingest(
        SOURCE_DATABASE,
        [database_message(901, 13, "报销单周五前交", created_at=BASE + 40)],
        conversations=[conversation(13, "财务对接", source=SOURCE_DATABASE, last=BASE + 41)],
        coverage=[CoverageRecord(source=SOURCE_DATABASE, status=COVERAGE_COMPLETE)], now=2.0,
    )
    logical = store.link_observation(kind=LINK_KIND_CONVERSATION, observation_canonical_id=cid(SOURCE_VISUAL, 13),
                                     logical_id=None, basis=LINK_OPERATOR, asserted_by="test", now=3.0)
    store.link_observation(kind=LINK_KIND_CONVERSATION, observation_canonical_id=cid(SOURCE_DATABASE, 13),
                           logical_id=logical, basis=LINK_OPERATOR, asserted_by="test", now=3.0)
    return logical


@pytest.fixture()
def service(store):
    populate(store)
    return MemoryQueryService(store)


# --- matching -------------------------------------------------------------------


def test_normalisation_is_nfc_trim_casefold():
    assert normalize_name("  Design Review ") == "design review"
    assert normalize_name("é") == normalize_name("é")
    assert normalize_name(None) == ""


def test_a_unique_title_resolves_to_one_candidate(service):
    result = service.conversations(name="设计评审群")
    assert result.is_unique and not result.is_ambiguous
    item = result.items[0]
    assert item.canonical_conversation_id == cid(SOURCE_VISUAL, 11)
    assert item.match == MATCH_EXACT and item.display_name == "设计评审群"
    assert item.logical_conversation_id is None and item.sources == (SOURCE_VISUAL,)


def test_an_ambiguous_title_stays_two_candidates(service):
    result = service.conversations(name="产品群")
    assert result.is_ambiguous and len(result.items) == 2
    assert {i.canonical_conversation_id for i in result.items} == {cid(SOURCE_VISUAL, 7), cid(SOURCE_VISUAL, 9)}
    assert all(i.match == MATCH_EXACT for i in result.items)
    # Most recently seen first, deterministic.
    assert [i.canonical_conversation_id for i in result.items] == [cid(SOURCE_VISUAL, 9), cid(SOURCE_VISUAL, 7)]


def test_exact_ranks_before_contains_and_contains_is_a_substring_only(service):
    result = service.conversations(name="评审")
    assert [i.match for i in result.items] == [MATCH_CONTAINS]
    assert result.items[0].display_name == "设计评审群"
    assert service.conversations(name="评 审").items == ()  # no fuzzy, no token matching
    assert service.conversations(name="群产品").items == ()  # substring, not bag of characters


def test_matching_is_case_and_whitespace_insensitive_but_nothing_else(store):
    MemoryIngestor(store).ingest(SOURCE_VISUAL, [], conversations=[conversation(21, "Design Review", last=1.0)], now=1.0)
    service = MemoryQueryService(store)
    assert service.conversations(name="  design review ").items[0].match == MATCH_EXACT
    assert service.conversations(name="review").items[0].match == MATCH_CONTAINS
    assert service.conversations(name="reveiw").items == ()


def test_no_name_lists_everything_most_recent_first(service):
    result = service.conversations()
    assert all(i.match == MATCH_ALL for i in result.items)
    names = [i.display_name for i in result.items]
    assert names == ["财务对接", "设计评审群", "产品群", "产品群"]
    assert result.coverage.exhaustive_of_store is True


def test_limit_and_truncation(service):
    result = service.conversations(limit=2)
    assert len(result.items) == 2 and result.truncated
    assert result.coverage.exhaustive_of_store is False


def test_discovery_is_deterministic(service):
    first = [i.canonical_conversation_id for i in service.conversations(name="产品群").items]
    assert first == [i.canonical_conversation_id for i in service.conversations(name="产品群").items]


# --- logical conversations -------------------------------------------------------


def test_linked_observations_group_into_one_logical_candidate(store):
    logical = populate(store)
    result = MemoryQueryService(store).conversations(name="财务对接")
    assert result.is_unique
    item = result.items[0]
    assert item.logical_conversation_id == logical
    assert set(item.sources) == {SOURCE_VISUAL, SOURCE_DATABASE}
    assert {o.canonical_conversation_id for o in item.observations} == {cid(SOURCE_VISUAL, 13), cid(SOURCE_DATABASE, 13)}
    # Representative is the most recently seen observation, deterministically.
    assert item.canonical_conversation_id == cid(SOURCE_DATABASE, 13)


def test_same_name_across_sources_is_not_merged_without_a_link(store):
    ingestor = MemoryIngestor(store)
    ingestor.ingest(SOURCE_VISUAL, [], conversations=[conversation(5, "同名群", last=1.0)], now=1.0)
    ingestor.ingest(SOURCE_DATABASE, [], conversations=[conversation(5, "同名群", source=SOURCE_DATABASE, last=2.0)], now=2.0)
    result = MemoryQueryService(store).conversations(name="同名群")
    assert result.is_ambiguous and len(result.items) == 2
    assert {i.logical_conversation_id for i in result.items} == {None}


def test_a_candidate_is_sufficient_to_continue(service):
    item = service.conversations(name="设计评审群").items[0]
    hits = service.search(text="评审", conversation_canonical_id=item.canonical_conversation_id).items
    assert [h.text for h in hits] == ["评审定在周四十点"]
    assert service.timeline(item.canonical_conversation_id).items
    assert service.recent_context(conversation_canonical_id=item.canonical_conversation_id).items


# --- the public tool ---------------------------------------------------------------


@pytest.fixture()
def served(tmp_path, monkeypatch):
    with MemoryStore.open(granted(tmp_path)) as store:
        populate(store)
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(consent.MEMORY_DB_PATH_ENV, str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(server, "resolve_consent", lambda: consent.resolve_consent(None, lambda: app_state(True)))
    return tmp_path


def test_the_tool_returns_the_discovery_envelope(served):
    response = call(server.memory_conversations, name="产品群")
    assert response["ok"] is True
    assert {"items", "truncated", "query_scope", "coverage", "candidates", "unique", "ambiguous"} <= set(response)
    assert (response["candidates"], response["unique"], response["ambiguous"]) == (2, False, True)
    assert response["query_scope"]["kind"] == "conversations"
    assert "no fuzzy" in response["query_scope"]["match_rule"]
    assert response["coverage"]["status"] == "store_inventory"
    assert response["coverage"]["exhaustive_of_store"] is True
    for item in response["items"]:
        assert {"canonical_conversation_id", "logical_conversation_id", "display_name", "kind", "match",
                "sources", "observations"} == set(item)
        assert {"canonical_conversation_id", "source", "source_conversation_id", "display_name", "kind",
                "first_seen_at", "last_seen_at"} == set(item["observations"][0])


def test_the_tool_never_claims_exhaustive_of_the_source(served):
    response = call(server.memory_conversations)
    assert "exhaustive_of_source" not in response["coverage"]
    assert "never observed" in response["coverage"]["note"]


def test_the_tool_validates_and_caps(served):
    assert call(server.memory_conversations, name="x" * 201)["state"] == "invalid_argument"
    assert call(server.memory_conversations, limit="all")["state"] == "invalid_argument"
    assert call(server.memory_conversations, limit=10_000)["query_scope"]["limit"] == server.MAX_LIMIT
    assert call(server.memory_conversations, name="   ")["query_scope"]["name"] is None


def test_the_tool_leaks_nothing(served, tmp_path):
    import getpass, socket
    blob = json.dumps(call(server.memory_conversations), ensure_ascii=False)
    assert str(tmp_path) not in blob and "sqlite" not in blob
    assert socket.gethostname() not in blob and getpass.getuser() not in blob


def test_the_tool_refuses_without_consent(tmp_path, monkeypatch):
    monkeypatch.setenv(consent.MEMORY_ENABLED_ENV, "1")
    monkeypatch.setenv(consent.MEMORY_DB_PATH_ENV, str(tmp_path / "memory.sqlite"))
    monkeypatch.setattr(server, "resolve_consent", lambda: consent.resolve_consent(None, lambda: None))
    assert call(server.memory_conversations)["state"] == "consent_state_missing"


def test_the_existing_message_tools_did_not_grow_a_name_parameter():
    import inspect
    for tool in (server.memory_search, server.memory_timeline, server.memory_recent, server.memory_context):
        assert "name" not in inspect.signature(getattr(tool, "fn", tool)).parameters


# --- live server process -------------------------------------------------------------


async def _discover_then_query(env):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=[str(Path(server.__file__))], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = [t.name for t in (await session.list_tools()).tools]

            async def tool(tool_name, **arguments):
                result = await session.call_tool(tool_name, arguments)
                return json.loads("".join(getattr(c, "text", "") for c in result.content))
            unique = await tool("memory_conversations", name="设计评审群")
            ambiguous = await tool("memory_conversations", name="产品群")
            chosen = unique["items"][0]["canonical_conversation_id"]
            followed = await tool("memory_search", text="评审", conversation_id=chosen)
            return names, unique, ambiguous, followed


def test_discovery_over_a_real_server_process(tmp_path):
    with MemoryStore.open(granted(tmp_path)) as store:
        populate(store)
    home = tmp_path / "home"
    prefs = home / "Library" / "Preferences"; prefs.mkdir(parents=True)
    (prefs / f"{consent.APP_PREFERENCE_DOMAIN}.plist").write_bytes(
        plistlib.dumps({consent.CONSENT_STATE_KEY: app_state(True)}))
    env = {"HOME": str(home), "PATH": str(tmp_path / "nopath"), "LANG": "en_US.UTF-8",
           consent.MEMORY_ENABLED_ENV: "1", consent.MEMORY_DB_PATH_ENV: str(tmp_path / "memory.sqlite"),
           "PYTHONPATH": os.pathsep.join(sys.path)}
    names, unique, ambiguous, followed = asyncio.run(_discover_then_query(env))
    assert set(names) == set(server.TOOL_NAMES) and len(names) == 5
    assert unique["unique"] is True and unique["items"][0]["display_name"] == "设计评审群"
    assert ambiguous["ambiguous"] is True and ambiguous["candidates"] == 2
    assert [i["text"] for i in followed["items"]] == ["评审定在周四十点"]
    assert followed["coverage"]["status"] == "observed_complete" and followed["items"]
