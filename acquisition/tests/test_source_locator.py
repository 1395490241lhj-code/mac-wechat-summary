"""The recorded source decision: read back, structurally validated, fail closed.

This contract is the production input the composition root consumes. It is a
reader of one app-owned record and nothing else: it does not search for WeChat
data, does not choose an account, and does not open, snapshot, decrypt or
validate a database. It never carries key material.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT, ROOT / "bridge"):
    sys.path.insert(0, str(path))

from acquisition import (  # noqa: E402
    AcquisitionSourceSet,
    EncryptedSource,
    KeyDescriptor,
    LocatedSource,
    SourceLocator,
)
from acquisition.source_locator import (  # noqa: E402
    MANIFEST_VERSION,
    SOURCE_ABSENT,
    SOURCE_MALFORMED,
    SOURCE_READY,
    SOURCE_STATES,
    SOURCE_UNSUPPORTED_VERSION,
)

FINGERPRINT = "a" * 64
COMPATIBILITY = "b" * 64


def _descriptor(role: str = "messages") -> dict[str, object]:
    return {
        "source_fingerprint": FINGERPRINT,
        "role_token": role,
        "record_format_version": 1,
        "compatibility_token": COMPATIBILITY,
    }


def _entry(main: str, role: str = "messages") -> dict[str, object]:
    return {"main": main, "wal": None, "shm": None, "key_descriptor": _descriptor(role)}


def _manifest(**overrides) -> dict[str, object]:
    document: dict[str, object] = {
        "manifest_version": MANIFEST_VERSION,
        "selected_source": "database",
        "messages": [_entry("/private/var/db/message_0.db")],
        "conversation_identity": _entry("/private/var/db/session.db", "session"),
        "display_identity": _entry("/private/var/db/contact.db", "contact"),
    }
    document.update(overrides)
    return document


def _write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "database_source.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_no_recorded_decision_is_unavailable(tmp_path):
    located = SourceLocator(tmp_path / "absent.json").resolve()

    assert located.state == SOURCE_ABSENT
    assert located.source_set is None


def test_a_valid_record_reconstructs_the_source_set(tmp_path):
    located = SourceLocator(_write(tmp_path, _manifest())).resolve()

    assert located.state == SOURCE_READY
    source_set = located.source_set
    assert isinstance(source_set, AcquisitionSourceSet)
    assert len(source_set.message_sources) == 1
    assert source_set.conversation_identity_source is not None
    assert source_set.display_identity_source is not None
    assert source_set.message_sources[0].main == Path("/private/var/db/message_0.db")
    assert source_set.message_sources[0].key_descriptor == KeyDescriptor(
        FINGERPRINT, "messages", 1, COMPATIBILITY)


def test_optional_identity_roles_may_be_absent(tmp_path):
    document = _manifest()
    document["conversation_identity"] = None
    document["display_identity"] = None

    located = SourceLocator(_write(tmp_path, document)).resolve()

    assert located.state == SOURCE_READY
    assert located.source_set.conversation_identity_source is None
    assert located.source_set.display_identity_source is None


@pytest.mark.parametrize(
    "document",
    [
        {"manifest_version": MANIFEST_VERSION},
        _manifest(messages=[]),
        _manifest(messages="not-a-list"),
        _manifest(selected_source="visual"),
        _manifest(messages=[_entry("")]),
        _manifest(messages=[_entry("relative/path.db")]),
        _manifest(messages=[_entry("/x.db", "")]),
        _manifest(messages=[{"main": "/x.db", "wal": 5, "shm": None,
                             "key_descriptor": _descriptor()}]),
    ],
)
def test_a_malformed_record_is_unavailable_and_content_free(tmp_path, document):
    secret = "fixture_secret_path_marker"
    document = json.loads(json.dumps(document).replace("/x.db", f"/{secret}.db"))

    located = SourceLocator(_write(tmp_path, document)).resolve()

    assert located.state == SOURCE_MALFORMED
    assert located.source_set is None
    assert secret not in located.state


def test_invalid_json_is_unavailable(tmp_path):
    path = tmp_path / "database_source.json"
    path.write_text("{not json", encoding="utf-8")

    located = SourceLocator(path).resolve()

    assert located.state == SOURCE_MALFORMED
    assert located.source_set is None


@pytest.mark.parametrize("version", [0, 2, "1", None])
def test_an_unsupported_manifest_version_is_refused(tmp_path, version):
    located = SourceLocator(
        _write(tmp_path, _manifest(manifest_version=version))).resolve()

    assert located.state == SOURCE_UNSUPPORTED_VERSION
    assert located.source_set is None


def test_the_same_encrypted_path_may_not_fill_two_roles(tmp_path):
    document = _manifest(
        messages=[_entry("/private/var/db/shared.db")],
        conversation_identity=_entry("/private/var/db/shared.db", "session"),
    )

    located = SourceLocator(_write(tmp_path, document)).resolve()

    assert located.state == SOURCE_MALFORMED
    assert located.source_set is None


def test_located_source_state_and_payload_cannot_disagree(tmp_path):
    source_set = AcquisitionSourceSet((
        EncryptedSource(
            Path("/private/var/db/message_0.db"), None, None,
            KeyDescriptor(FINGERPRINT, "messages", 1, COMPATIBILITY)),
    ))

    assert LocatedSource(SOURCE_READY, source_set).source_set is source_set
    with pytest.raises(ValueError, match="^located source contradiction$"):
        LocatedSource(SOURCE_READY)
    with pytest.raises(ValueError, match="^located source contradiction$"):
        LocatedSource(SOURCE_ABSENT, source_set)
    with pytest.raises(ValueError, match="^located source state invalid$"):
        LocatedSource("fixture_secret_state")


def test_the_recorded_representation_never_carries_key_material(tmp_path):
    secret = "fixture_secret_key_bytes"
    document = _manifest()
    text = json.dumps(document)

    assert secret not in text
    for key in ("secret", "key_bytes", "password", "salt"):
        assert key not in text
    located = SourceLocator(_write(tmp_path, document)).resolve()
    assert secret not in repr(located)


def test_the_locator_never_uses_the_legacy_recursive_discovery():
    source = (ROOT / "acquisition" / "source_locator.py").read_text(encoding="utf-8")

    for forbidden in ("auto_detect", "os.walk", "rglob", "glob", "scandir",
                      "listdir", "iterdir", "xwechat_files", "db_storage"):
        assert forbidden not in source


def test_the_manifest_vocabulary_is_closed():
    assert SOURCE_STATES == frozenset({
        SOURCE_READY, SOURCE_ABSENT, SOURCE_UNSUPPORTED_VERSION, SOURCE_MALFORMED,
    })
