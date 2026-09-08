import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from core.archive_importer import ArchiveImportError, ArchiveStore, parse_transcript, read_wechat_archive


def transcript(rows):
    return "".join(f"·{sender}\n{stamp}\n{text}\n" for sender, stamp, text in rows)


def write_zip(path: Path, rows, *, txt_name="聊天记录.txt", extra=None):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(txt_name, transcript(rows))
        for name, body in (extra or {}).items():
            zf.writestr(name, body)


class TranscriptTests(unittest.TestCase):
    def test_parse_preserves_order_and_duplicate_occurrences(self):
        rows = [
            ("张三", "2026年9月7日 20:35", "哈哈"),
            ("张三", "2026年9月7日 20:35", "哈哈"),
            ("李四", "2026年9月7日 20:36", "收到"),
        ]
        parsed = parse_transcript(transcript(rows))
        self.assertEqual([r.sender for r in parsed], ["张三", "张三", "李四"])
        self.assertEqual([r.text for r in parsed], ["哈哈", "哈哈", "收到"])
        self.assertEqual([r.occurrence for r in parsed], [0, 1, 0])
        self.assertEqual([r.sequence for r in parsed], [0, 1, 2])

    def test_rejects_non_native_text(self):
        with self.assertRaises(ArchiveImportError):
            parse_transcript("plain text")


class ArchiveTests(unittest.TestCase):
    def test_reads_largest_valid_transcript(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "sample.zip"
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("small.txt", transcript([("A", "2026年9月7日 20:35", "1")]))
                zf.writestr("big.txt", transcript([
                    ("A", "2026年9月7日 20:35", "1"),
                    ("B", "2026年9月7日 20:36", "2"),
                ]))
            name, records = read_wechat_archive(path)
            self.assertEqual(name, "big.txt")
            self.assertEqual(len(records), 2)

    def test_rejects_traversal_entry(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "unsafe.zip"
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("../聊天记录.txt", transcript([("A", "2026年9月7日 20:35", "x")]))
            with self.assertRaises(ArchiveImportError):
                read_wechat_archive(path)


class StoreTests(unittest.TestCase):
    def test_import_is_idempotent_and_preserves_raw_zip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "store"
            path = Path(td) / "sample.zip"
            rows = [
                ("A", "2026年9月7日 20:35", "hello"),
                ("B", "2026年9月7日 20:36", "world"),
            ]
            write_zip(path, rows)
            store = ArchiveStore(root)
            first = store.import_zip(path, conversation_name="测试群")
            second = store.import_zip(path, conversation_name="测试群")
            self.assertFalse(first.existing_archive)
            self.assertEqual(first.inserted_message_count, 2)
            self.assertTrue(second.existing_archive)
            self.assertEqual(second.inserted_message_count, 0)
            self.assertTrue(Path(first.raw_path).is_file())
            self.assertEqual(store.message_count("name:测试群"), 2)

    def test_overlapping_exports_dedupe_but_keep_real_duplicates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "store"
            first = Path(td) / "first.zip"
            second = Path(td) / "second.zip"
            shared = [
                ("A", "2026年9月7日 20:35", "哈哈"),
                ("A", "2026年9月7日 20:35", "哈哈"),
                ("B", "2026年9月7日 20:36", "收到"),
            ]
            write_zip(first, [("Z", "2026年9月7日 20:34", "before"), *shared])
            write_zip(second, [*shared, ("C", "2026年9月7日 20:37", "after")])
            store = ArchiveStore(root)
            r1 = store.import_zip(first, conversation_name="测试群")
            r2 = store.import_zip(second, conversation_name="测试群")
            self.assertEqual(r1.inserted_message_count, 4)
            self.assertEqual(r2.inserted_message_count, 1)
            self.assertEqual(store.message_count("name:测试群"), 5)

            conn = sqlite3.connect(root / "messages.sqlite3")
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE sender='A' AND text='哈哈'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
