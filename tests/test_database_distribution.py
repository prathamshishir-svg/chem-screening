import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import database_distribution as distribution
from database_distribution import DatabaseDistributionError


def make_database(path):
    con = sqlite3.connect(path)
    for table in ("chemicals", "rxn_records", "ord_reaction_records", "epa_toxval_assertions"):
        con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)")
        con.execute(f"INSERT INTO {table}(value) VALUES ('valid')")
    con.commit()
    con.close()


class DatabaseDistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_existing_database_is_never_replaced(self):
        destination = self.root / "chem.db"
        make_database(destination)
        original = destination.read_bytes()
        with mock.patch.object(distribution, "_download_resumable",
                               side_effect=AssertionError("network must not be used")):
            result = distribution.install_database({"url": "https://example.invalid/db",
                                                    "sha256": "0" * 64}, destination)
        self.assertEqual(result, "already_present")
        self.assertEqual(destination.read_bytes(), original)

    def test_invalid_existing_database_is_preserved_and_refused(self):
        destination = self.root / "chem.db"
        destination.write_bytes(b"user data")
        original = destination.read_bytes()
        with mock.patch.object(distribution, "_download_resumable",
                               side_effect=AssertionError("existing file must not be replaced")):
            with self.assertRaisesRegex(DatabaseDistributionError, "Refusing to overwrite"):
                distribution.install_database({"url": "https://example.invalid/db",
                                                "sha256": "0" * 64}, destination)
        self.assertEqual(destination.read_bytes(), original)

    def test_verified_download_installs_atomically(self):
        source = self.root / "source.db"
        make_database(source)
        payload = source.read_bytes()
        destination = self.root / "install" / "chem.db"
        with mock.patch.object(distribution, "_download_resumable",
                               side_effect=lambda _url, part: part.write_bytes(payload)):
            config = {"url": "https://example.invalid/db", "sha256": hashlib.sha256(payload).hexdigest(),
                      "size_bytes": len(payload)}
            result = distribution.install_database(config, destination)
        self.assertEqual(result, "installed")
        self.assertEqual(destination.read_bytes(), payload)
        self.assertFalse(destination.with_name("chem.db.download.part").exists())

    def test_hash_mismatch_does_not_create_database(self):
        source = self.root / "source.db"
        make_database(source)
        destination = self.root / "chem.db"
        with mock.patch.object(distribution, "_download_resumable",
                               side_effect=lambda _url, part: part.write_bytes(source.read_bytes())):
            with self.assertRaisesRegex(DatabaseDistributionError, "SHA-256"):
                distribution.install_database({"url": "https://example.invalid/db",
                                               "sha256": "0" * 64}, destination)
        self.assertFalse(destination.exists())

    def test_manifest_requires_configured_artifact_and_https(self):
        manifest = self.root / "release.json"
        manifest.write_text(json.dumps({"url": None, "sha256": None}), encoding="utf-8")
        with self.assertRaisesRegex(DatabaseDistributionError, "not published/configured"):
            distribution.load_release_config(manifest)
        with self.assertRaisesRegex(DatabaseDistributionError, "HTTPS"):
            distribution.load_release_config(manifest, url="http://example.invalid/db", sha256="a" * 64)


if __name__ == "__main__":
    unittest.main()
