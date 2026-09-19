"""The synthetic substrate speaks the parser's dialect and really has parts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import wechatdb

from . import fixtures
from .fixtures import ALPHA, BETA, SyntheticMessage

CONVERSATION = ALPHA

MESSAGES = (
    SyntheticMessage(1, 1_756_000_010, ALPHA, "fixture message one"),
    SyntheticMessage(2, 1_756_000_020, BETA, "fixture message two"),
    SyntheticMessage(3, 1_756_000_030, ALPHA, "fixture message three"),
    SyntheticMessage(4, 1_756_000_040, BETA, "fixture message four"),
    SyntheticMessage(5, 1_756_000_050, ALPHA, "fixture message five"),
    SyntheticMessage(6, 1_756_000_060, BETA, "fixture message six"),
)


def test_a_synthetic_part_is_parseable_by_the_leaf_parser(tmp_path):
    """The builder writes what the real parser reads. No mock, no parser edit."""
    part = fixtures.readable_part(tmp_path, "fixture_readable.db",
                                  CONVERSATION, MESSAGES[:3])

    records = fixtures.parse_part(part, CONVERSATION)

    assert [r.local_id for r in records] == [1, 2, 3]
    assert [r.timestamp for r in records] == [1_756_000_010, 1_756_000_020,
                                              1_756_000_030]
    assert [r.sender_id for r in records] == [ALPHA, BETA, ALPHA]
    assert [r.content for r in records] == ["fixture message one",
                                            "fixture message two",
                                            "fixture message three"]
    assert all(r.message_type == "text" for r in records)
    # Unnamed by the caller, the parser keeps the digest and invents nothing.
    digest = fixtures.conversation_table(CONVERSATION)[len("Msg_"):]
    assert all(r.session_id == f"msg_{digest}" for r in records)
    assert all(isinstance(r, wechatdb.MessageRecord) for r in records)


def test_a_multi_part_fixture_really_has_several_parts(tmp_path):
    """The whole conversation exists only in the union of the parts.

    Without this, a later multi-part test could pass against a fixture that
    someone had quietly collapsed back into one database.
    """
    parts = fixtures.split_conversation(tmp_path, CONVERSATION, MESSAGES, parts=3)

    assert len(parts) >= 2
    assert len({p.path for p in parts}) == len(parts)
    table = fixtures.conversation_table(CONVERSATION)
    for part in parts:
        connection = part.open()
        try:
            assert table in wechatdb.conversation_tables(connection)
        finally:
            connection.close()

    expected = {(m.local_id, m.create_time, m.sender, m.text) for m in MESSAGES}
    seen_by_part = []
    for part in parts:
        records = fixtures.parse_part(part, CONVERSATION)
        seen = {(r.local_id, r.timestamp, r.sender_id, r.content) for r in records}
        assert seen, part.name
        assert seen < expected, part.name  # strict subset, never the whole
        seen_by_part.append(seen)

    assert set.union(*seen_by_part) == expected
    assert sum(len(s) for s in seen_by_part) == len(expected)  # no duplicates

    with pytest.raises(ValueError):
        fixtures.split_conversation(tmp_path, CONVERSATION, MESSAGES, parts=1,
                                    stem="fixture_refused")


def test_every_needed_shape_is_representable(tmp_path):
    """Each later matrix row has a substrate, and each behaves as named."""
    readable = fixtures.readable_part(tmp_path, "fixture_a.db", CONVERSATION,
                                      MESSAGES[:2])
    unknown = fixtures.unknown_name_part(tmp_path, CONVERSATION, MESSAGES[:1])
    unopenable = fixtures.unopenable_part()
    unrecognised = fixtures.unrecognised_schema_part(tmp_path)
    unbounded = fixtures.unbounded_part(tmp_path, CONVERSATION)

    assert len(fixtures.parse_part(readable, CONVERSATION)) == 2

    assert not re.match(r".*\.db$", unknown.name) and unknown.path is not None
    assert len(fixtures.parse_part(unknown, CONVERSATION)) == 1

    assert unopenable.path is None
    with pytest.raises(fixtures.SyntheticOpenFailure):
        unopenable.open()

    connection = unrecognised.open()
    try:
        assert wechatdb.conversation_tables(connection) == []
    finally:
        connection.close()

    assert fixtures.parse_part(unbounded, CONVERSATION) == []
    connection = unbounded.open()
    try:
        assert fixtures.conversation_table(CONVERSATION) in \
            wechatdb.conversation_tables(connection)
        low, high = connection.execute(
            f'SELECT MIN(create_time), MAX(create_time) FROM '
            f'"{fixtures.conversation_table(CONVERSATION)}"').fetchone()
        assert (low, high) == (None, None)
    finally:
        connection.close()


FORBIDDEN = ("Library", "Containers", "/Users/", "com.tencent",
             "xwechat_files", "db_storage")


def test_no_fixture_carries_a_real_path_or_identifier(tmp_path):
    """The substrate is fabricated and says so.

    The fixture *definitions* are scanned, not the pytest temporary directory:
    where the generated files land is pytest's business, and the point is
    that nothing here hard-codes a real place or a plausible identity.
    """
    here = Path(__file__).resolve().parent
    sources = {p.name: p.read_text(encoding="utf-8")
               for p in (here / "fixtures.py", here / "test_fixtures.py",
                         here / "test_isolation.py", here / "conftest.py")}

    for name, text in sources.items():
        for marker in FORBIDDEN:
            # The two guard modules name the markers in order to forbid them;
            # the fixture module and conftest may not contain them at all.
            if name not in ("test_fixtures.py", "test_isolation.py"):
                assert marker not in text, (name, marker)
        # An identifier has a body after the underscore; the pattern spelling
        # itself therefore never matches its own source.
        for match in re.finditer(r"wxid_[A-Za-z0-9]\w*", text):
            assert match.group(0).startswith("wxid_fixture_"), (name, match.group(0))

    parts = [
        fixtures.readable_part(tmp_path, "fixture_p.db", CONVERSATION, MESSAGES[:1]),
        fixtures.unrecognised_schema_part(tmp_path),
        fixtures.unbounded_part(tmp_path, CONVERSATION),
        *fixtures.split_conversation(tmp_path, CONVERSATION, MESSAGES[:2]),
    ]
    for part in parts:
        assert part.path is not None
        assert part.path.is_relative_to(tmp_path), part.name
        assert not part.path.is_absolute() or part.path.is_relative_to(tmp_path)
    assert fixtures.unopenable_part().path is None
