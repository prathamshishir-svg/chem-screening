import gzip
import hashlib
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cloud_database import CloudDatabaseError, bootstrap_database


def _make_database(path, reaction_count=3):
    connection = sqlite3.connect(path)
    try:
        for table in (
            "chemicals", "family_rules", "group_rules", "reactions", "reaction_products",
            "templates", "transport_hazards", "pair_reactions", "pair_products", "aliases",
            "rxn_molecules", "ord_reaction_records", "ord_reaction_participants",
            "ord_chemical_structures", "dataset_source", "dataset_release", "import_run",
            "epa_toxval_assertions",
        ):
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
        connection.execute("CREATE TABLE rxn_records (id INTEGER)")
        connection.executemany("INSERT INTO rxn_records VALUES (?)",
                               [(i,) for i in range(reaction_count)])
        connection.commit()
    finally:
        connection.close()


class _Response(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(payload)
        self.headers = Message()

    def getcode(self):
        return self.status


class CloudDatabaseTests(unittest.TestCase):
    def _artifact(self, root, reaction_count=3):
        source = root / "source.db"
        _make_database(source, reaction_count)
        database_bytes = source.read_bytes()
        compressed = gzip.compress(database_bytes)
        config = {
            "url": "https://example.org/releases/chem.db.gz",
            "asset_sha256": hashlib.sha256(compressed).hexdigest(),
            "database_sha256": hashlib.sha256(database_bytes).hexdigest(),
            "asset_size_bytes": len(compressed),
            "expected_reaction_count": reaction_count,
        }
        return compressed, database_bytes, config

    def test_missing_release_url_keeps_existing_bootstrap_path(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(bootstrap_database(Path(temp), secrets={}, environ={}))

    def test_download_verifies_checksum_and_database_then_reuses_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            compressed, database_bytes, config = self._artifact(root)
            secrets = {"database": config}
            with patch("cloud_database.urllib.request.urlopen",
                       return_value=_Response(compressed)) as download:
                installed = bootstrap_database(root, secrets=secrets, environ={})
                reused = bootstrap_database(root, secrets=secrets, environ={})

            self.assertEqual(installed, reused)
            self.assertTrue(installed.is_file())
            self.assertEqual(installed.read_bytes(), database_bytes)
            self.assertEqual(download.call_count, 1)

    def test_failed_checksum_does_not_activate_an_incomplete_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            compressed, _, config = self._artifact(root)
            config["asset_sha256"] = "0" * 64
            with patch("cloud_database.urllib.request.urlopen",
                       return_value=_Response(compressed)):
                with self.assertRaisesRegex(CloudDatabaseError, "SHA-256"):
                    bootstrap_database(root, secrets={"database": config}, environ={})

            self.assertFalse(list((root / ".chem_database_cache").glob("chem.db-*.db")))

    def test_existing_local_database_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "chem.db"
            _make_database(original, reaction_count=1)
            original_bytes = original.read_bytes()
            compressed, _, config = self._artifact(root, reaction_count=3)
            with patch("cloud_database.urllib.request.urlopen",
                       return_value=_Response(compressed)):
                selected = bootstrap_database(root, secrets={"database": config}, environ={})

            self.assertNotEqual(selected, original)
            self.assertEqual(original.read_bytes(), original_bytes)
            self.assertTrue(selected.is_file())

    def test_gemini_secret_and_request_path_remain_available_with_mocked_client(self):
        import ai_explain

        streamlit_stub = SimpleNamespace(secrets={"GEMINI_API_KEY": "test-only-key"})
        response = SimpleNamespace(text='{"summary":"mocked"}')
        client = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=response)))
        with patch.dict(sys.modules, {"streamlit": streamlit_stub}), \
                patch.dict(os.environ, {"GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""}), \
                patch("google.genai.Client", return_value=client) as client_factory:
            key = ai_explain.get_api_key()
            result = ai_explain._call_model("test prompt", "gemini-2.5-flash", key)

        self.assertEqual(result, response.text)
        client_factory.assert_called_once_with(api_key="test-only-key")
        client.models.generate_content.assert_called_once()
        self.assertEqual(client.models.generate_content.call_args.kwargs["model"],
                         "gemini-2.5-flash")


if __name__ == "__main__":
    unittest.main()
