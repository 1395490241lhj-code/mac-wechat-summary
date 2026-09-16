"""Every synthetic 4.1+ shape resolves to one :class:`MessageRecord`.

No test here reads a real WeChat database. Each builds its own SQLite file from
``fixtures`` and asserts on what the parser makes of it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import fields as dataclass_fields

import pytest

from wechatdb import parser
from wechatdb.msg_types import unpack_local_type
from wechatdb.parser import MessageRecord, normalise_timestamp, parse_database

from . import fixtures

DIRECT = "wxid_fixture_b"
GROUP = "7700000001@chatroom"

IMAGE_DIGEST = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
VIDEO_DIGEST = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"

QUOTE_TYPE = (57 << 32) | 49
LINK_TYPE = (5 << 32) | 49
FILE_TYPE = (6 << 32) | 49

SECONDS = 1_756_000_000
MILLISECONDS = 1_756_000_200_123


@pytest.fixture()
def database(tmp_path):
    """The nine required shapes, in two conversations, in one database."""
    path = tmp_path / "message_0.db"
    connection = fixtures.new_database(path)
    ids = fixtures.add_names(
        connection, ["wxid_fixture_a", "wxid_fixture_b", "wxid_fixture_c"]
    )

    direct = fixtures.add_conversation(connection, DIRECT)
    fixtures.insert(
        connection, direct, server_id=1001, local_type=1, create_time=SECONDS,
        real_sender_id=ids["wxid_fixture_b"],
        message_content=b"Hello from a fixture.",
    )
    fixtures.insert(
        connection, direct, server_id=1002, local_type=1, create_time=SECONDS + 100,
        real_sender_id=ids["wxid_fixture_b"],
        message_content=fixtures.compress("Compressed fixture line."),
    )
    fixtures.insert(
        connection, direct, server_id=1003, local_type=1, create_time=MILLISECONDS,
        real_sender_id=ids["wxid_fixture_b"],
        message_content=b"Stamped in milliseconds.",
    )
    fixtures.insert(
        connection, direct, server_id=1004, local_type=3, create_time=SECONDS + 300,
        real_sender_id=ids["wxid_fixture_b"],
        message_content=fixtures.compress(
            fixtures.image_xml(media_digest=IMAGE_DIGEST)
        ),
        packed_info_data=fixtures.packed_info(IMAGE_DIGEST),
    )
    fixtures.insert(
        connection, direct, server_id=1005, local_type=34, create_time=SECONDS + 400,
        real_sender_id=ids["wxid_fixture_b"],
        message_content=fixtures.voice_xml().encode("utf-8"),
    )
    fixtures.insert(
        connection, direct, server_id=1006, local_type=43, create_time=SECONDS + 500,
        real_sender_id=ids["wxid_fixture_b"],
        message_content=fixtures.compress(
            fixtures.video_xml(media_digest=VIDEO_DIGEST)
        ),
    )
    fixtures.insert(
        connection, direct, server_id=1007, local_type=LINK_TYPE,
        create_time=SECONDS + 600, real_sender_id=ids["wxid_fixture_b"],
        message_content=fixtures.compress(
            fixtures.link_xml(title="A fixture article")
        ),
    )
    fixtures.insert(
        connection, direct, server_id=1008, local_type=FILE_TYPE,
        create_time=SECONDS + 700, real_sender_id=ids["wxid_fixture_b"],
        message_content=fixtures.compress(fixtures.file_xml(title="notes.pdf")),
    )

    group = fixtures.add_conversation(connection, GROUP)
    fixtures.insert(
        connection, group, server_id=2001, local_type=1, create_time=SECONDS + 10,
        real_sender_id=ids["wxid_fixture_c"],
        message_content=fixtures.compress("Group line from C."),
    )
    fixtures.insert(
        connection, group, server_id=2002, local_type=1, create_time=SECONDS + 20,
        message_content="wxid_fixture_a:\nLegacy prefixed line.".encode("utf-8"),
    )
    fixtures.insert(
        connection, group, server_id=2003, local_type=QUOTE_TYPE,
        create_time=SECONDS + 30, real_sender_id=ids["wxid_fixture_c"],
        message_content=fixtures.compress(
            "wxid_fixture_c:\n"
            + fixtures.quote_xml(
                title="Agreed", quoted_server_id=2001, quoted_text="Group line from C."
            )
        ),
    )
    fixtures.insert(
        connection, group, server_id=2004, local_type=10000,
        create_time=SECONDS + 40,
        message_content=fixtures.revoke_xml(who="Fixture C").encode("utf-8"),
    )
    fixtures.insert(
        connection, group, server_id=2005, local_type=1, create_time=SECONDS + 50,
        message_content=b"Sender only in source.",
        source=fixtures.compress(
            fixtures.source_xml(real_chat_user="wxid_fixture_b")
        ),
    )
    connection.commit()
    yield connection
    connection.close()


def records(connection, **kwargs) -> list[MessageRecord]:
    """Every record in the database.

    Deliberately a flat list: ``local_id`` restarts at 1 in each conversation
    table, so keying records by it would silently drop one conversation's rows.
    """
    return list(parse_database(connection, **kwargs))


# -- discovery -----------------------------------------------------------------

def test_conversation_tables_are_found_by_shape(database):
    tables = parser.conversation_tables(database)
    assert tables == sorted(
        [fixtures.table_name(DIRECT), fixtures.table_name(GROUP)]
    )


def test_a_table_that_merely_starts_with_msg_is_not_a_conversation(tmp_path):
    connection = fixtures.new_database(tmp_path / "m.db")
    connection.execute("CREATE TABLE Msg_notadigest (local_id INTEGER)")
    connection.execute('CREATE TABLE "Msg_deadbeef" (local_id INTEGER)')
    assert parser.conversation_tables(connection) == []


def test_name2id_maps_rowids_to_usernames(database):
    mapping = parser.load_name2id(database)
    assert mapping == {
        1: "wxid_fixture_a",
        2: "wxid_fixture_b",
        3: "wxid_fixture_c",
    }


def test_a_database_without_name2id_still_parses(tmp_path):
    connection = sqlite3.connect(str(tmp_path / "bare.db"))
    table = fixtures.add_conversation(connection, DIRECT)
    fixtures.insert(
        connection, table, local_type=1, create_time=SECONDS,
        message_content=b"No name table here.",
    )
    assert parser.load_name2id(connection) == {}
    [record] = list(parse_database(connection))
    assert record.content == "No name table here."
    assert record.sender_id is None


# -- session identity ----------------------------------------------------------

def test_an_unresolved_conversation_keeps_its_digest_and_is_not_named(database):
    parsed = records(database)
    sessions = {record.session_id for record in parsed}
    assert sessions == {
        f"msg_{fixtures.digest(DIRECT)}",
        f"msg_{fixtures.digest(GROUP)}",
    }


def test_a_supplied_name_replaces_the_digest(database):
    parsed = records(
        database, session_names={fixtures.digest(GROUP): GROUP}
    )
    sessions = {record.session_id for record in parsed}
    assert GROUP in sessions
    assert f"msg_{fixtures.digest(DIRECT)}" in sessions


# -- local_type ----------------------------------------------------------------

@pytest.mark.parametrize(
    ("packed", "expected"),
    [
        (1, (1, None)),
        (10000, (10000, None)),
        (49, (49, None)),
        (QUOTE_TYPE, (49, 57)),
        (LINK_TYPE, (49, 5)),
        (244813135921, (49, 57)),
        (None, (0, None)),
    ],
)
def test_local_type_unpacking(packed, expected):
    assert unpack_local_type(packed) == expected


def test_a_bare_49_is_classified_from_its_xml(tmp_path):
    connection = fixtures.new_database(tmp_path / "bare49.db")
    table = fixtures.add_conversation(connection, DIRECT)
    fixtures.insert(
        connection, table, local_type=49, create_time=SECONDS,
        message_content=fixtures.compress(
            fixtures.quote_xml(title="Bare", quoted_server_id=7, quoted_text="x")
        ),
    )
    [record] = list(parse_database(connection))
    assert record.message_type == "quote"
    assert record.reply_to == "7"


def test_an_unrecognised_type_is_unknown_not_invented(tmp_path):
    connection = fixtures.new_database(tmp_path / "odd.db")
    table = fixtures.add_conversation(connection, DIRECT)
    fixtures.insert(
        connection, table, local_type=48, create_time=SECONDS,
        message_content=b"<msg><location label='somewhere' /></msg>",
    )
    [record] = list(parse_database(connection))
    assert record.message_type == "unknown"


# -- timestamps ----------------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (SECONDS, SECONDS),
        (MILLISECONDS, 1_756_000_200),
        ("1756000000", SECONDS),
        (0, 0),
        (None, 0),
        ("not a time", 0),
    ],
)
def test_timestamps_normalise_to_seconds(raw, expected):
    assert normalise_timestamp(raw) == expected


def test_a_millisecond_row_is_seconds_in_the_record(database):
    parsed = records(database)
    [row] = [r for r in parsed if r.content == "Stamped in milliseconds."]
    assert row.timestamp == 1_756_000_200


# -- content and kinds ---------------------------------------------------------

def by_content(parsed, text):
    return next(r for r in parsed if r.content == text)


def test_plain_text_is_carried_through(database):
    parsed = records(database)
    record = by_content(parsed, "Hello from a fixture.")
    assert record.message_type == "text"
    assert record.sender_id == "wxid_fixture_b"
    assert record.media_id is None
    assert record.reply_to is None


def test_zstd_text_is_decompressed(database):
    parsed = records(database)
    record = by_content(parsed, "Compressed fixture line.")
    assert record.message_type == "text"


def test_an_image_carries_a_media_id_and_no_invented_placeholder(database):
    parsed = records(database)
    [record] = [r for r in parsed if r.message_type == "image"]
    assert record.media_id == IMAGE_DIGEST
    assert record.content is None


def test_voice_and_video_are_distinguished(database):
    parsed = records(database)
    [voice] = [r for r in parsed if r.message_type == "voice"]
    [video] = [r for r in parsed if r.message_type == "video"]
    assert voice.content is None and voice.media_id is None
    assert video.media_id == VIDEO_DIGEST


def test_a_link_and_a_file_keep_their_titles(database):
    parsed = records(database)
    [link] = [r for r in parsed if r.message_type == "link"]
    [file_record] = [r for r in parsed if r.message_type == "file"]
    assert link.content == "A fixture article"
    assert file_record.content == "notes.pdf"


def test_a_quote_reports_the_message_it_answers(database):
    parsed = records(database)
    [quote] = [r for r in parsed if r.message_type == "quote"]
    assert quote.content == "Agreed"
    assert quote.reply_to == "2001"
    assert quote.sender_id == "wxid_fixture_c"


def test_a_system_message_is_rendered_not_dropped(database):
    parsed = records(database)
    [system] = [r for r in parsed if r.message_type == "system"]
    assert system.content == "Fixture C recalled a message"


# -- senders -------------------------------------------------------------------

def test_a_group_sender_comes_from_real_sender_id(database):
    parsed = records(database)
    record = by_content(parsed, "Group line from C.")
    assert record.sender_id == "wxid_fixture_c"


def test_the_legacy_payload_prefix_is_still_read_and_stripped(database):
    parsed = records(database)
    record = by_content(parsed, "Legacy prefixed line.")
    assert record.sender_id == "wxid_fixture_a"


def test_source_names_the_sender_when_nothing_else_does(database):
    parsed = records(database)
    record = by_content(parsed, "Sender only in source.")
    assert record.sender_id == "wxid_fixture_b"


def test_a_display_name_is_injected_never_guessed(database):
    parsed = records(database, display_names={"wxid_fixture_c": "Fixture C"})
    record = by_content(parsed, "Group line from C.")
    assert record.sender_name == "Fixture C"
    other = by_content(parsed, "Hello from a fixture.")
    assert other.sender_name == "wxid_fixture_b"


# -- the unified shape ---------------------------------------------------------

REQUIRED_FIELDS = {
    "session_id", "local_id", "timestamp", "sender_id", "sender_name",
    "message_type", "content", "media_id", "reply_to",
}


def test_the_record_is_exactly_the_agreed_shape():
    assert {field.name for field in dataclass_fields(MessageRecord)} == REQUIRED_FIELDS


def test_every_fixture_row_becomes_one_well_formed_record(database):
    parsed = records(database)
    assert len(parsed) == 13
    for record in parsed:
        assert isinstance(record, MessageRecord)
        assert record.session_id
        assert isinstance(record.local_id, int) and record.local_id > 0
        assert isinstance(record.timestamp, int) and record.timestamp > 0
        assert record.message_type in {
            "text", "image", "voice", "video", "file", "link", "quote",
            "system", "unknown",
        }


def test_records_are_ordered_oldest_first_within_a_conversation(database):
    parsed = records(database)
    for session in {r.session_id for r in parsed}:
        stamps = [r.timestamp for r in parsed if r.session_id == session]
        assert stamps == sorted(stamps)


# -- the boundary this layer keeps ---------------------------------------------

def test_the_parser_holds_no_key_path_or_decryption():
    """Checked against imports and identifiers, not raw text.

    The modules describe in prose what they deliberately avoid, and prose
    naming a thing is not a dependency on it. The bridge's own boundary test
    draws the line the same way.
    """
    import ast
    from pathlib import Path

    for module in ("parser.py", "msg_types.py"):
        tree = ast.parse(
            (Path(parser.__file__).parent / module).read_text(encoding="utf-8")
        )
        imported: set[str] = set()
        identifiers: set[str] = set()
        literals: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, (ast.Name, ast.arg)):
                identifiers.add(getattr(node, "id", None) or node.arg)
            elif isinstance(node, ast.Attribute):
                identifiers.add(node.attr)
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                identifiers.add(node.name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.append(node.value)

        # No process, no crypto, no filesystem. sqlite3 is here only to accept
        # a connection the caller already opened.
        assert imported <= {
            "__future__", "re", "sqlite3", "dataclasses",
            "collections", "zstandard", "msg_types", "",
        }, (module, imported)

        joined = " ".join(identifiers).lower()
        for forbidden in ("sqlcipher", "passphrase", "keychain", "decrypt",
                          "subprocess", "tencent", "open", "path"):
            assert forbidden not in joined, (module, forbidden)

        # Docstrings may name what is avoided; executable strings may not carry
        # a WeChat location or a cipher pragma.
        for literal in literals:
            if literal in ast.get_docstring(tree) or "":
                continue
            lowered = literal.lower()
            for forbidden in ("pragma key", "xwechat_files", "containers",
                              "com.tencent", "/users/"):
                assert forbidden not in lowered, (module, literal)
