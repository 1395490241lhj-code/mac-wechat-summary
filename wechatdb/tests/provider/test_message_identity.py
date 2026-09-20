"""Provider-local public message identity and ordering, from synthetic records."""

from __future__ import annotations

import importlib
from dataclasses import replace

import pytest

from wechatdb.parser import MessageRecord


def owner():
    try:
        return importlib.import_module("wechatdb.provider.message_identity")
    except ModuleNotFoundError:
        pytest.fail("provider-local message identity owner is missing")


def record(*, local_id=1, timestamp=100, server_id=None, session="fixture_session",
           sender="Fixture Sender", content="fixture content"):
    return MessageRecord(
        session_id=session,
        local_id=local_id,
        timestamp=timestamp,
        sender_id="wxid_fixture_sender",
        sender_name=sender,
        message_type="text",
        content=content,
        media_id=None,
        reply_to=None,
        server_id=server_id,
    )


def test_sequence_is_signed_64_and_sorts_exactly_by_timestamp_then_local_id():
    identity = owner()
    records = [
        record(timestamp=2, local_id=0),
        record(timestamp=1, local_id=2),
        record(timestamp=1, local_id=1),
    ]
    assert [identity.message_sequence(item) for item in sorted(
        records, key=identity.message_sequence)] == [
            (1 << 31) | 1,
            (1 << 31) | 2,
            2 << 31,
        ]
    assert identity.message_sequence(record(
        timestamp=(1 << 32) - 1, local_id=(1 << 31) - 1)) == (1 << 63) - 1


@pytest.mark.parametrize("field,value", [
    ("timestamp", True),
    ("timestamp", -1),
    ("timestamp", 1 << 32),
    ("timestamp", 1.0),
    ("local_id", False),
    ("local_id", -1),
    ("local_id", 1 << 31),
    ("local_id", "1"),
])
def test_sequence_refuses_values_outside_its_bit_allocation(field, value):
    with pytest.raises(ValueError, match="^message sequence input is invalid$"):
        owner().message_sequence(replace(record(), **{field: value}))


def test_message_id_prefers_server_id_and_uses_a_stable_negative_fallback():
    identity = owner()
    assert identity.message_id(record(server_id=77)) == 77

    absent = record(server_id=None)
    fallback = identity.message_id(absent)
    assert -(1 << 63) <= fallback < 0
    assert identity.message_id(replace(
        absent, sender_name="Different display", content="Different content")) == fallback
    assert identity.message_id(replace(absent, timestamp=101)) != fallback
    assert identity.message_id(replace(absent, local_id=2)) != fallback
    assert identity.message_id(replace(absent, session_id="other_session")) != fallback
