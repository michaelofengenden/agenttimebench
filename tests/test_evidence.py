"""Use real files to check immutable, content-addressed fixture evidence."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agenttime.evidence import EvidenceStore, IntegrityError, canonical_json


class CanonicalJsonTests(unittest.TestCase):
    def test_json_encoding_is_order_independent_compact_utf8(self):
        self.assertEqual(canonical_json({"z": "café", "a": [True, None, 1]}),
                         b'{"a":[true,null,1],"z":"caf\xc3\xa9"}')
        self.assertEqual(canonical_json({"b": 2, "a": {"y": 4, "x": 3}}),
                         b'{"a":{"x":3,"y":4},"b":2}')

    def test_nonfinite_numbers_are_not_json_evidence(self):
        for number in [float("nan"), float("inf"), float("-inf")]:
            with self.subTest(number=number), self.assertRaises(ValueError):
                canonical_json({"nested": [number]})

    def test_non_json_keys_and_values_cannot_be_silently_coerced(self):
        for value in [{1: "integer-key"}, {True: "boolean-key"}, (1, 2), {"set": {1}}, b"bytes"]:
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                canonical_json(value)

    def test_circular_values_are_rejected(self):
        value = []
        value.append(value)
        with self.assertRaises(ValueError):
            canonical_json(value)


class EvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.root = self.directory / "evidence"
        self.store = EvidenceStore(self.root)

    def test_put_returns_content_digest_and_get_returns_exact_bytes(self):
        digest = self.store.put(b"abc")
        self.assertEqual(digest, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(self.store.get(digest), b"abc")
        self.assertEqual((self.root / digest).read_bytes(), b"abc")

    def test_binary_and_empty_evidence_round_trip(self):
        for data in [b"", bytes(range(256))]:
            with self.subTest(length=len(data)):
                self.assertEqual(self.store.get(self.store.put(data)), data)

    def test_repeated_put_verifies_and_preserves_existing_object(self):
        digest = self.store.put(b"abc")
        before = (self.root / digest).stat()
        self.assertEqual(self.store.put(b"abc"), digest)
        after = (self.root / digest).stat()
        self.assertEqual(before.st_ino, after.st_ino)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual([path.name for path in self.root.iterdir()], [digest])

    def test_duplicate_put_does_not_return_before_syncing_publication(self):
        digest = self.store.put(b"abc")
        # Another publisher may have linked the complete object but not yet
        # synced its directory. A successful duplicate put must also sync it.
        with patch("agenttime.evidence.os.fsync", side_effect=OSError("sync failed")):
            with self.assertRaises(OSError):
                self.store.put(b"abc")
        self.assertEqual(self.store.get(digest), b"abc")

    def test_corruption_is_reported_by_get(self):
        digest = self.store.put(b"abc")
        (self.root / digest).write_bytes(b"corrupt")
        with self.assertRaises(IntegrityError):
            self.store.get(digest)

    def test_put_never_repairs_corruption_by_overwriting_it(self):
        digest = self.store.put(b"abc")
        (self.root / digest).write_bytes(b"corrupt")
        with self.assertRaises(IntegrityError):
            self.store.put(b"abc")
        self.assertEqual((self.root / digest).read_bytes(), b"corrupt")
        self.assertTrue(issubclass(IntegrityError, ValueError))

    def test_concurrent_puts_publish_complete_objects_without_overwriting(self):
        payloads = [b"same" * 100_000 if i % 2 else b"other" * 100_000 for i in range(24)]
        def put(data):
            return EvidenceStore(self.root).put(data)
        with ThreadPoolExecutor(max_workers=8) as pool:
            digests = list(pool.map(put, payloads))
        self.assertEqual(len(set(digests)), 2)
        for digest, data in zip(digests, payloads):
            self.assertEqual(self.store.get(digest), data)
        self.assertEqual({path.name for path in self.root.iterdir()}, set(digests))

    def test_invalid_digest_cannot_escape_store(self):
        sentinel = self.directory / "sentinel"
        sentinel.write_bytes(b"outside")
        for digest in ["../sentinel", str(sentinel), "a" * 63, "A" * 64,
                       "a" * 64 + "/../sentinel", "a" * 64 + "\n", None, 1, b"a" * 64]:
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                self.store.get(digest)
        self.assertEqual(sentinel.read_bytes(), b"outside")

    def test_missing_object_stays_missing(self):
        with self.assertRaises(FileNotFoundError):
            self.store.get("a" * 64)

    def test_object_symlinks_are_rejected_on_reads_and_puts(self):
        digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        target = self.directory / "outside"
        target.write_bytes(b"abc")
        (self.root / digest).symlink_to(target)
        with self.assertRaises(IntegrityError):
            self.store.get(digest)
        with self.assertRaises(IntegrityError):
            self.store.put(b"abc")
        self.assertEqual(target.read_bytes(), b"abc")
        self.assertTrue((self.root / digest).is_symlink())

    def test_symlink_root_is_rejected(self):
        link = self.directory / "linked-store"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(IntegrityError):
            EvidenceStore(link)

    def test_replaced_root_cannot_redirect_an_existing_store(self):
        digest = self.store.put(b"abc")
        moved = self.directory / "moved"
        self.root.rename(moved)
        self.root.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(IntegrityError):
            self.store.get(digest)
        with self.assertRaises(IntegrityError):
            self.store.put(b"abc")

    def test_nonregular_object_is_an_integrity_error(self):
        digest = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        (self.root / digest).mkdir()
        with self.assertRaises(IntegrityError):
            self.store.get(digest)
        with self.assertRaises(IntegrityError):
            self.store.put(b"abc")

    def test_put_requires_bytes_instead_of_implicit_encoding(self):
        for data in ["abc", bytearray(b"abc"), None]:
            with self.subTest(data=data), self.assertRaises(TypeError):
                self.store.put(data)


if __name__ == "__main__":
    unittest.main()
