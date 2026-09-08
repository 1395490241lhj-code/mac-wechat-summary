"""Tests for the Dukou/native WeChat archive importer.

The dedup tests are the point of this file: overlapping exports must not
duplicate messages, and must never delete a real one.
"""
import os
import sqlite3
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.archive_importer import (
    ArchiveImportError,
    ArchiveStore,
    merge_bucket,
    parse_transcript,
    read_wechat_archive,
)

M35 = "2026年9月7日 20:35"
M36 = "2026年9月7日 20:36"


def transcript(rows, *, newline="\n", bom=False):
    body = "".join(f"·{sender}\n{stamp}\n{text}\n" for sender, stamp, text in rows)
    body = body.replace("\n", newline)
    return ("\ufeff" if bom else "") + body


def write_zip(path: Path, rows, *, txt_name="聊天记录.txt", extra=None, **kwargs):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(txt_name, transcript(rows, **kwargs))
        for name, data in (extra or {}).items():
            zf.writestr(name, data)


def texts(store, key):
    return [(row["sender"], row["text"]) for row in store.messages(key)]


# ---------------------------------------------------------------- parser


class TranscriptTests(unittest.TestCase):
    def test_parses_plain_multi_message_transcript(self):
        rows = [("张三", M35, "哈哈"), ("张三", M35, "哈哈"), ("李四", M36, "收到")]
        parsed = parse_transcript(transcript(rows))
        self.assertEqual([r.sender for r in parsed], ["张三", "张三", "李四"])
        self.assertEqual([r.text for r in parsed], ["哈哈", "哈哈", "收到"])
        self.assertEqual([r.sequence for r in parsed], [0, 1, 2])
        self.assertEqual([r.timestamp_text for r in parsed], [M35, M35, M36])
        self.assertLess(parsed[0].timestamp, parsed[2].timestamp)

    def test_multi_line_and_empty_bodies(self):
        body = (
            f"·张三\n{M35}\n第一行\n第二行\n\n第四行\n"
            f"·李四\n{M35}\n\n"
            f"·王五\n{M36}\n尾巴\n"
        )
        parsed = parse_transcript(body)
        self.assertEqual(parsed[0].text, "第一行\n第二行\n\n第四行")
        self.assertEqual(parsed[1].text, "")
        self.assertEqual(parsed[2].text, "尾巴")

    def test_crlf_bom_unicode_and_emoji(self):
        rows = [("李·四", M35, "你好 🌍\n第二行 😀"), ("王五", M36, "Ünïcødé")]
        parsed = parse_transcript(transcript(rows, newline="\r\n", bom=True))
        self.assertEqual(parsed[0].sender, "李·四")
        self.assertEqual(parsed[0].text, "你好 🌍\n第二行 😀")
        self.assertEqual(parsed[1].text, "Ünïcødé")

    def test_rejects_non_native_text(self):
        with self.assertRaises(ArchiveImportError):
            parse_transcript("plain text")

    def test_rejects_text_with_content_before_the_first_record(self):
        with self.assertRaises(ArchiveImportError):
            parse_transcript(f"导出说明\n·张三\n{M35}\n哈哈\n")

    def test_tolerates_leading_blank_lines(self):
        parsed = parse_transcript("\n\n" + transcript([("张三", M35, "哈哈")]))
        self.assertEqual(len(parsed), 1)


# ------------------------------------------------------------ zip safety


class ArchiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def _zip_with_entry(self, name, body=None):
        path = self.td / "unsafe.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(name, body if body is not None else transcript([("A", M35, "x")]))
        return path

    def test_valid_zip_is_readable(self):
        path = self.td / "ok.zip"
        write_zip(path, [("A", M35, "1"), ("B", M36, "2")], extra={"img/1.png": b"\x89PNG"})
        entry, records = read_wechat_archive(path)
        self.assertEqual(entry, "聊天记录.txt")
        self.assertEqual(len(records), 2)

    def test_rejects_traversal_entry(self):
        with self.assertRaises(ArchiveImportError):
            read_wechat_archive(self._zip_with_entry("../聊天记录.txt"))

    def test_rejects_absolute_entry(self):
        with self.assertRaises(ArchiveImportError):
            read_wechat_archive(self._zip_with_entry("/etc/聊天记录.txt"))

    def test_rejects_windows_drive_entry(self):
        with self.assertRaises(ArchiveImportError):
            read_wechat_archive(self._zip_with_entry("C:\\聊天记录.txt"))

    def test_rejects_archive_without_a_transcript(self):
        path = self.td / "no-transcript.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("readme.txt", "just some notes\nnot a chat")
            zf.writestr("img/1.png", b"\x89PNG")
        with self.assertRaises(ArchiveImportError):
            read_wechat_archive(path)

    def test_rejects_too_many_entries(self):
        path = self.td / "many.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for i in range(1001):
                zf.writestr(f"f{i}.bin", b"x")
        with self.assertRaises(ArchiveImportError):
            read_wechat_archive(path)

    def test_rejects_unsupported_compression(self):
        path = self.td / "bzip.zip"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_BZIP2) as zf:
            zf.writestr("聊天记录.txt", transcript([("A", M35, "x")]))
        with self.assertRaises(ArchiveImportError):
            read_wechat_archive(path)

    def test_rejects_non_zip(self):
        path = self.td / "not.zip"
        path.write_bytes(b"definitely not a zip")
        with self.assertRaises(ArchiveImportError):
            read_wechat_archive(path)

    def test_detects_crc_corruption(self):
        path = self.td / "ok.zip"
        write_zip(path, [("A", M35, "hello world hello world")])
        blob = bytearray(path.read_bytes())
        blob[-40] ^= 0xFF
        (self.td / "bad.zip").write_bytes(bytes(blob))
        with self.assertRaises(ArchiveImportError):
            read_wechat_archive(self.td / "bad.zip")

    def test_picks_the_transcript_with_the_most_messages(self):
        path = self.td / "multi.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("readme.txt", "not a transcript")
            zf.writestr("small.txt", transcript([("A", M35, "1")]))
            zf.writestr("big.txt", transcript([("A", M35, "1"), ("B", M36, "2")]))
        entry, records = read_wechat_archive(path)
        self.assertEqual(entry, "big.txt")
        self.assertEqual(len(records), 2)


# ------------------------------------------------------- merge algorithm


class MergeBucketTests(unittest.TestCase):
    def merged(self, stored, new):
        return merge_bucket(stored, new)[0]

    def test_empty_store(self):
        self.assertEqual(self.merged([], ["a", "b"]), ["a", "b"])

    def test_identical_window_adds_nothing(self):
        self.assertEqual(self.merged(["a", "b"], ["a", "b"]), ["a", "b"])

    def test_contained_window_adds_nothing(self):
        self.assertEqual(self.merged(["a", "b", "c"], ["b"]), ["a", "b", "c"])

    def test_new_window_completes_a_truncated_bucket(self):
        self.assertEqual(self.merged(["b"], ["a", "b", "c"]), ["a", "b", "c"])

    def test_appends_the_tail(self):
        self.assertEqual(self.merged(["a", "b"], ["b", "c"]), ["a", "b", "c"])

    def test_prepends_the_head(self):
        self.assertEqual(self.merged(["b", "c"], ["a", "b"]), ["a", "b", "c"])

    def test_repeated_items_align_on_the_longest_overlap(self):
        self.assertEqual(
            self.merged(["k", "x", "k"], ["k", "y", "k"]),
            ["k", "x", "k", "y", "k"],
        )

    def test_disjoint_windows_keep_everything(self):
        self.assertEqual(self.merged(["a"], ["b"]), ["a", "b"])

    def test_periodic_bucket_is_ambiguous_and_resolves_conservatively(self):
        """Pins the documented limit: idempotency wins over recovering a repeat.

        ``["q", "p"]`` plus ``["p", "q", "p"]`` is equally consistent with a
        true history of ``[q, p, q, p]`` and with one of ``[p, q, p]``.  We must
        pick the shorter reading, because the alternative duplicates every
        conversation on re-import.  Never a *spurious* message -- only, in this
        degenerate case, a missing repeat.
        """
        self.assertEqual(self.merged(["q", "p"], ["p", "q", "p"]), ["p", "q", "p"])
        self.assertEqual(self.merged(["哈", "哈"], ["哈", "哈"]), ["哈", "哈"])


# --------------------------------------------------------------- store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.td = Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.store = ArchiveStore(self.td / "store")

    def zip_of(self, name, rows):
        path = self.td / name
        write_zip(path, rows)
        return path

    def test_import_is_idempotent_and_preserves_the_raw_zip(self):
        path = self.zip_of("sample.zip", [("A", M35, "hello"), ("B", M36, "world")])
        first = self.store.import_zip(path, conversation_name="测试群")
        second = self.store.import_zip(path, conversation_name="测试群")

        self.assertFalse(first.existing_archive)
        self.assertEqual(first.inserted_message_count, 2)
        self.assertEqual(first.conversation_key, "name:测试群")
        self.assertTrue(second.existing_archive)
        self.assertEqual(second.inserted_message_count, 0)
        self.assertEqual(self.store.message_count("name:测试群"), 2)

        raw = Path(first.raw_path)
        self.assertTrue(raw.is_file())
        self.assertEqual(raw.read_bytes(), path.read_bytes())
        self.assertEqual(raw.name, f"{first.archive_id}.zip")
        self.assertEqual(raw.parent.name, first.archive_id[:2])

    def test_reimport_after_the_source_file_disappears(self):
        path = self.zip_of("sample.zip", [("A", M35, "hello")])
        result = self.store.import_zip(path, conversation_name="测试群")
        path.unlink()
        entry, records = read_wechat_archive(result.raw_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(entry, result.transcript_entry)

    def test_reimport_of_identical_content_under_a_new_store(self):
        path = self.zip_of("sample.zip", [("A", M35, "hello")])
        self.store.import_zip(path, conversation_name="测试群")
        again = ArchiveStore(self.td / "store").import_zip(path, conversation_name="测试群")
        self.assertTrue(again.existing_archive)

    def test_archive_and_store_permissions_are_tight(self):
        self.zip_of("sample.zip", [("A", M35, "hello")])
        result = self.store.import_zip(self.td / "sample.zip", conversation_name="测试群")
        self.assertEqual(stat.S_IMODE(os.stat(self.store.root).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.raw_dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.db_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(result.raw_path).st_mode), 0o600)

    def test_overlapping_exports_dedupe_but_keep_real_duplicates(self):
        shared = [("A", M35, "哈哈"), ("A", M35, "哈哈"), ("B", M36, "收到")]
        first = self.zip_of("first.zip", [("Z", "2026年9月7日 20:34", "before"), *shared])
        second = self.zip_of("second.zip", [*shared, ("C", "2026年9月7日 20:37", "after")])

        r1 = self.store.import_zip(first, conversation_name="测试群")
        r2 = self.store.import_zip(second, conversation_name="测试群")
        self.assertEqual(r1.inserted_message_count, 4)
        self.assertEqual(r2.inserted_message_count, 1)
        self.assertEqual(self.store.message_count("name:测试群"), 5)
        self.assertEqual(
            texts(self.store, "name:测试群"),
            [("Z", "before"), ("A", "哈哈"), ("A", "哈哈"), ("B", "收到"), ("C", "after")],
        )

    def test_repeated_identical_messages_in_one_minute_are_all_kept(self):
        path = self.zip_of("rep.zip", [("A", M35, "哈哈")] * 3)
        result = self.store.import_zip(path, conversation_name="测试群")
        self.assertEqual(result.inserted_message_count, 3)
        self.assertEqual(self.store.message_count("name:测试群"), 3)
        self.store.import_zip(self.zip_of("rep2.zip", [("A", M35, "哈哈")] * 3),
                              conversation_name="测试群")
        self.assertEqual(self.store.message_count("name:测试群"), 3)

    def test_shifted_window_does_not_drop_a_repeated_message(self):
        """Regression: a per-key occurrence counter loses the third 哈哈 here.

        The true history has three identical messages in one minute.  Two
        overlapping export windows each see two of them; a naive "nth
        occurrence" identity renumbers on the second window and collides.
        """
        full = [
            ("A", M35, "哈哈"),
            ("A", M35, "x"),
            ("A", M35, "哈哈"),
            ("A", M35, "y"),
            ("A", M35, "哈哈"),
        ]
        first = self.zip_of("w1.zip", full[:3])
        second = self.zip_of("w2.zip", full[2:])
        self.store.import_zip(first, conversation_name="测试群")
        self.store.import_zip(second, conversation_name="测试群")
        self.assertEqual(self.store.message_count("name:测试群"), 5)
        self.assertEqual(texts(self.store, "name:测试群"), [(s, t) for s, _, t in full])

    def test_older_export_imported_second_is_prepended(self):
        full = [("A", M35, "1"), ("A", M35, "2"), ("A", M35, "3"), ("A", M35, "4")]
        newer = self.zip_of("newer.zip", full[2:])
        older = self.zip_of("older.zip", full[:3])
        self.store.import_zip(newer, conversation_name="测试群")
        r2 = self.store.import_zip(older, conversation_name="测试群")
        self.assertEqual(r2.inserted_message_count, 2)
        self.assertEqual(texts(self.store, "name:测试群"), [(s, t) for s, _, t in full])

    def test_occurrence_is_renumbered_after_a_prepend(self):
        full = [
            ("A", M35, "哈哈"),
            ("A", M35, "a"),
            ("A", M35, "b"),
            ("A", M35, "哈哈"),
            ("A", M35, "c"),
        ]
        self.store.import_zip(self.zip_of("tail.zip", full[2:]), conversation_name="测试群")
        rows = self.store.messages("name:测试群")
        self.assertEqual([r["occurrence"] for r in rows], [0, 0, 0])

        self.store.import_zip(self.zip_of("head.zip", full[:4]), conversation_name="测试群")
        rows = self.store.messages("name:测试群")
        self.assertEqual([(r["sender"], r["text"]) for r in rows], [(s, t) for s, _, t in full])
        # The 哈哈 stored first is now the *second* one in the minute.
        self.assertEqual([r["occurrence"] for r in rows], [0, 0, 0, 1, 0])
        self.assertEqual(len({r["fingerprint"] for r in rows}), 5)

    def test_equal_overlap_in_both_directions_keeps_every_message(self):
        """Ambiguous alignment: order is a guess, but nothing may be lost."""
        self.store.import_zip(
            self.zip_of("t.zip", [("A", M35, "x"), ("A", M35, "哈哈")]),
            conversation_name="测试群",
        )
        self.store.import_zip(
            self.zip_of("h.zip", [("A", M35, "哈哈"), ("A", M35, "x")]),
            conversation_name="测试群",
        )
        self.assertEqual(self.store.message_count("name:测试群"), 3)

    def test_distinct_conversations_do_not_share_messages(self):
        rows = [("A", M35, "hello")]
        self.store.import_zip(self.zip_of("a.zip", rows), conversation_name="群一")
        self.store.import_zip(
            self.zip_of("b.zip", [*rows, ("A", M36, "x")]), conversation_name="群二"
        )
        self.assertEqual(self.store.message_count("name:群一"), 1)
        self.assertEqual(self.store.message_count("name:群二"), 2)

    def test_explicit_conversation_key_overrides_the_name(self):
        path = self.zip_of("a.zip", [("A", M35, "hello")])
        result = self.store.import_zip(
            path, conversation_name="测试群", conversation_key="wxid:abc123"
        )
        self.assertEqual(result.conversation_key, "wxid:abc123")
        self.assertEqual(self.store.message_count("wxid:abc123"), 1)

    def test_archive_messages_maps_every_transcript_position(self):
        rows = [("A", M35, "1"), ("A", M35, "2"), ("B", M36, "3")]
        result = self.store.import_zip(self.zip_of("a.zip", rows), conversation_name="测试群")
        with sqlite3.connect(self.store.db_path) as conn:
            mapped = conn.execute(
                "SELECT sequence, m.text FROM archive_messages am "
                "JOIN messages m ON m.id = am.message_id "
                "WHERE am.archive_id = ? ORDER BY sequence",
                (result.archive_id,),
            ).fetchall()
        self.assertEqual(mapped, [(0, "1"), (1, "2"), (2, "3")])

    def test_failed_import_leaves_no_raw_copy_and_no_rows(self):
        bad = self.td / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("readme.txt", "not a transcript")
        with self.assertRaises(ArchiveImportError):
            self.store.import_zip(bad, conversation_name="测试群")
        self.assertEqual(list(self.store.raw_dir.rglob("*")), [])
        self.assertEqual(self.store.message_count("name:测试群"), 0)
        self.assertTrue(bad.is_file())

    def test_missing_and_unnamed_inputs_are_rejected(self):
        with self.assertRaises(ArchiveImportError):
            self.store.import_zip(self.td / "nope.zip", conversation_name="测试群")
        with self.assertRaises(ArchiveImportError):
            self.store.import_zip(
                self.zip_of("a.zip", [("A", M35, "x")]), conversation_name="   "
            )


if __name__ == "__main__":
    unittest.main()
