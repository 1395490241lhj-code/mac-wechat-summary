"""WeChat 4.1+ message-type decoding, over bytes that are already plaintext.

This module knows three things about a WeChat 4.1+ message row and nothing
else: how its payload columns may be compressed, how its ``local_type`` packs
two numbers into one integer, and which of the project's message kinds that
pair means.

**It never opens, decrypts, or locates a database.** There is no key, no cipher
parameter, no filesystem path, and no WeChat installation knowledge here. It is
given bytes and returns facts about them.

## ``local_type``

Regular messages carry a small type: 1 text, 3 image, 34 voice, 43 video, 47
sticker, 48 location, 50 call, 10000 system.

Application messages pack the ``<appmsg><type>`` value into the high 32 bits
and leave 49 in the low 32: ``local_type = (appmsg_type << 32) | 49``. A quoted
reply is ``(57 << 32) | 49 == 244813135921``. A bare 49 also occurs, and then
the appmsg type is only knowable from the decompressed XML.

## Compression

``message_content`` and ``source`` may be raw zstd frames, recognised by their
magic number. Decompression is attempted only when the magic is present, so an
uncompressed column is never mangled by a speculative decode. When
``zstandard`` is not installed a compressed column is reported as undecodable
rather than silently returned as bytes that merely look like text.
"""

from __future__ import annotations

import re

#: The four bytes that begin every zstd frame.
ZSTD_MAGIC: bytes = b"\x28\xb5\x2f\xfd"

#: The low 32 bits shared by every packed application message.
APPMSG_MARKER: int = 49

try:  # zstandard is declared in requirements.txt but is not required to import.
    import zstandard as _zstd

    _DECOMPRESSOR = _zstd.ZstdDecompressor()
except ImportError:  # pragma: no cover - exercised only on a bare interpreter
    _DECOMPRESSOR = None

# -- kinds ---------------------------------------------------------------------
#
# The vocabulary is the bridge's, not a new one: text, image, voice, video,
# file, link, quote, system, unknown. A type this module does not recognise
# becomes `unknown` rather than a newly invented kind.

KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_VOICE = "voice"
KIND_VIDEO = "video"
KIND_FILE = "file"
KIND_LINK = "link"
KIND_QUOTE = "quote"
KIND_SYSTEM = "system"
KIND_UNKNOWN = "unknown"

#: Regular ``local_type`` values. A sticker is an image; a location or a call
#: has no kind in this vocabulary and stays unknown rather than being forced.
_BASE_KINDS: dict[int, str] = {
    1: KIND_TEXT,
    3: KIND_IMAGE,
    34: KIND_VOICE,
    43: KIND_VIDEO,
    47: KIND_IMAGE,
    10000: KIND_SYSTEM,
}

#: ``<appmsg><type>`` values, for the packed form and for a bare 49 whose type
#: had to be read out of the XML.
_APPMSG_KINDS: dict[int, str] = {
    1: KIND_LINK,
    3: KIND_IMAGE,
    4: KIND_VIDEO,
    5: KIND_LINK,
    6: KIND_FILE,
    8: KIND_IMAGE,
    19: KIND_LINK,
    33: KIND_LINK,
    36: KIND_LINK,
    57: KIND_QUOTE,
    63: KIND_VIDEO,
    74: KIND_FILE,
    92: KIND_VOICE,
}


def maybe_decompress(value: object) -> bytes:
    """Returns the plaintext bytes of a payload column.

    ``value`` is whatever SQLite handed back: ``bytes``, ``str``, or ``None``.
    A zstd frame is decompressed; anything else is returned unchanged. An
    empty result means "nothing decodable here", which callers treat as absence
    rather than as an empty message.
    """
    if not value:
        return b""
    if isinstance(value, str):
        value = value.encode("utf-8", errors="ignore")
    if not isinstance(value, (bytes, bytearray, memoryview)):
        return b""
    data = bytes(value)
    if not data.startswith(ZSTD_MAGIC):
        return data
    if _DECOMPRESSOR is None:
        return b""
    try:
        return _DECOMPRESSOR.decompress(data)
    except Exception:
        return b""


def decode_text(value: object) -> str:
    """The plaintext of a payload column as text, or ``""``."""
    return maybe_decompress(value).decode("utf-8", errors="replace")


def unpack_local_type(local_type: object) -> tuple[int, int | None]:
    """Splits a ``local_type`` into ``(base_type, appmsg_type)``.

    ``appmsg_type`` is ``None`` unless the value is packed. A bare 49 is an
    application message whose type is not in the integer, so it reports
    ``(49, None)`` and the caller may still recover the type from the XML.
    """
    try:
        raw = int(local_type)
    except (TypeError, ValueError):
        return 0, None
    if raw > 0xFFFFFFFF and (raw & 0xFFFFFFFF) == APPMSG_MARKER:
        return APPMSG_MARKER, (raw >> 32) & 0xFFFFFFFF
    return raw, None


def classify(base_type: int, appmsg_type: int | None) -> str:
    """The message kind for a decoded ``local_type`` pair."""
    if base_type == APPMSG_MARKER:
        if appmsg_type is None:
            return KIND_UNKNOWN
        return _APPMSG_KINDS.get(appmsg_type, KIND_UNKNOWN)
    return _BASE_KINDS.get(base_type, KIND_UNKNOWN)


# -- XML ----------------------------------------------------------------------
#
# WeChat's payload XML is frequently not well-formed: unclosed tags, stray text
# before the declaration, CDATA in some builds and not others. A strict parser
# refuses the whole document over one such defect and loses the rest of the
# message, so these read one field at a time and return None when it is absent.

_CDATA = r"(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?"


def xml_tag(xml: str, tag: str) -> str | None:
    """The text of the first ``<tag>`` element, CDATA unwrapped."""
    if not xml:
        return None
    match = re.search(rf"<{tag}>{_CDATA}</{tag}>", xml, re.DOTALL)
    if match is None:
        return None
    return match.group(1).strip() or None


def xml_attr(xml: str, attr: str) -> str | None:
    """The value of the first ``attr="..."`` attribute anywhere in ``xml``."""
    if not xml:
        return None
    match = re.search(rf'\b{attr}="([^"]*)"', xml)
    if match is None:
        return None
    return match.group(1).strip() or None


def strip_markup(xml: str) -> str:
    """The XML with tags removed and whitespace collapsed."""
    plain = re.sub(r"<[^>]+>", " ", xml or "")
    return re.sub(r"\s+", " ", plain).strip()
