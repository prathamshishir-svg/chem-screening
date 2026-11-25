import sqlite3
import tempfile
import unittest
from pathlib import Path

from epa_diagnostics import collect_epa_diagnostics


class EpaDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "chem.db"
        connection = sqlite3.connect(self.database)
        try:
            connection.executescript(
                "CREATE TABLE dataset_source (id INTEGER PRIMARY KEY, source_key TEXT);"
                "CREATE TABLE dataset_release (id INTEGER PRIMARY KEY, source_id INTEGER, "
                "release_version TEXT, status TEXT);"
                "CREATE TABLE epa_toxval_chemicals (id INTEGER PRIMARY KEY, release_id INTEGER, "
                "casrn TEXT, name TEXT, dtxsid TEXT);"
                "CREATE TABLE epa_toxval_assertions (id INTEGER PRIMARY KEY, release_id INTEGER, "
                "chemical_id INTEGER);"
                "INSERT INTO dataset_source VALUES (1, 'epa-toxvaldb');"
                "INSERT INTO dataset_release VALUES (1, 1, '9.7.0', 'imported');"
                "INSERT INTO epa_toxval_chemicals VALUES "
                "(1, 1, '1912-24-9', 'Atrazine', 'DTXSID9020112');"
                "INSERT INTO epa_toxval_chemicals VALUES "
                "(2, 1, '1912-24-9', 'Atrazine', 'DTXSID9020112');"
                "INSERT INTO epa_toxval_chemicals VALUES "
                "(3, 1, '64-17-5', 'Ethanol', 'DTXSID9020566');"
                "INSERT INTO epa_toxval_assertions VALUES (1, 1, 1);"
                "INSERT INTO epa_toxval_assertions VALUES (2, 1, 2);"
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self):
        self.temp.cleanup()

    def test_diagnostic_reports_release_identity_and_lookup_counts(self):
        result = collect_epa_diagnostics(
            self.database,
            {"identifier": "Atrazine", "casrn": "1912-24-9", "limit": 25},
            {"records": [{"toxval_id": 1}], "total": 507, "match_basis": "CASRN"},
        )
        self.assertEqual(result["epa_release_version"], "9.7.0")
        self.assertEqual(result["epa_release_status"], "imported")
        self.assertEqual(result["total_epa_assertions"], 2)
        self.assertEqual(result["release_epa_assertions"], 2)
        self.assertEqual(result["atrazine_cas_identity_matches"], 2)
        self.assertEqual(result["lookup_assertions_returned"], 507)
        self.assertEqual(result["lookup_records_returned"], 1)
        self.assertEqual(result["lookup_match_basis"], "CASRN")
        self.assertEqual(result["lookup_arguments"], {
            "identifier": "Atrazine", "casrn": "1912-24-9", "limit": 25,
        })
        self.assertTrue(result["active_database_path"].startswith(("<HOME>/", "<REDACTED_PATH>/")))

    def test_identifier_reporting_omits_unapproved_fields(self):
        result = collect_epa_diagnostics(
            self.database,
            {"identifier": "Atrazine", "casrn": "1912-24-9", "api_key": "secret-value"},
            {"records": [], "total": 0},
        )
        self.assertNotIn("api_key", result["lookup_arguments"])
        self.assertNotIn("secret-value", repr(result))

    def test_missing_release_is_reported_without_raising(self):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("DELETE FROM dataset_release")
            connection.commit()
        finally:
            connection.close()
        result = collect_epa_diagnostics(self.database, {}, {"total": 0, "records": []})
        self.assertEqual(result["epa_release_version"], None)
        self.assertEqual(result["epa_release_status"], "not found")
        self.assertEqual(result["total_epa_assertions"], 2)
        self.assertEqual(result["database_diagnostic_status"], "ok")

    def test_missing_database_tables_return_safe_status(self):
        empty_db = Path(self.temp.name) / "empty.db"
        sqlite3.connect(empty_db).close()
        result = collect_epa_diagnostics(empty_db, {}, {"total": 0, "records": []})
        self.assertEqual(result["database_diagnostic_status"], "OperationalError")
        self.assertNotIn(self.temp.name, repr(result))


if __name__ == "__main__":
    unittest.main()
