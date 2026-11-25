import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import db
import migrations
from dataset_imports import register_dataset_release, register_dataset_source
from toxval_import import _assertion_tuple, parse_mysql_values


class ToxValTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "toxval-test.db"
        with closing(sqlite3.connect(self.db_path)) as con:
            con.executescript(
                "CREATE TABLE chemicals (cas TEXT PRIMARY KEY, name TEXT);"
                "CREATE TABLE pair_reactions (id INTEGER PRIMARY KEY, pair_key TEXT, status TEXT, checks TEXT);"
                "CREATE TABLE rxn_records (id INTEGER PRIMARY KEY, patent TEXT);"
            )
        migrations.migrate_database(self.db_path)
        source = register_dataset_source(
            self.db_path, source_key="epa-toxvaldb", name="EPA ToxValDB",
            publisher="U.S. EPA", canonical_url="https://www.epa.gov/comptox-tools/",
            license_name="CC0 1.0", attribution="U.S. EPA",
        )
        release = register_dataset_release(
            self.db_path, source_id=source["id"], release_key="toxvaldb:9.7.0",
            release_version="9.7.0", source_url="https://zenodo.org/records/17088058",
            retrieved_at="2026-11-25T00:00:00Z", content_checksum="a" * 64,
        )
        self.release_id = release["id"]
        with closing(sqlite3.connect(self.db_path)) as con, con:
            con.execute(
                "INSERT INTO epa_toxval_chemicals "
                "(id,release_id,source_chemical_id,casrn,name,dtxsid,local_match_type,raw_record_json) "
                "VALUES (1,?,?,?,?,?,?,?)",
                (self.release_id, "ToxVal00001", "64-17-5", "Ethanol", "DTXSID9020534",
                 "unmatched", json.dumps({"chemical_id": "ToxVal00001"})),
            )
            con.execute(
                "INSERT INTO epa_toxval_assertions "
                "(release_id,toxval_id,chemical_source_id,chemical_id,identity_match_status,dtxsid,"
                "source,experimental_record,toxval_type,toxval_numeric,toxval_numeric_raw,toxval_units,raw_record_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.release_id, 101, "ToxVal00001", 1, "unmatched", "DTXSID9020534",
                 "EPA source", "not experimental", "RfD", 0.5, "0.500", "mg/kg-day", "{}"),
            )
            con.execute(
                "INSERT INTO epa_toxval_record_sources "
                "(release_id,toxval_id,record_source_id,url,raw_record_json) VALUES (?,?,?,?,?)",
                (self.release_id, 101, 201, "https://example.org/source", "{}"),
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_searches_by_name_cas_and_dtxsid_and_reports_missing(self):
        with patch.object(db, "DB_FILE", str(self.db_path)):
            for identifier, kwargs, basis in (
                ("Ethanol", {}, "exact chemical name"),
                ("64-17-5", {}, "CASRN"),
                ("DTXSID9020534", {}, "DTXSID"),
            ):
                result = db.find_epa_toxval_records(identifier, **kwargs)
                self.assertEqual(result["total"], 1)
                self.assertEqual(result["match_basis"], basis)
                self.assertEqual(result["records"][0]["toxval_numeric_raw"], "0.500")
                self.assertEqual(result["records"][0]["experimental_record"], "not experimental")
                self.assertEqual(result["records"][0]["references"][0]["url"],
                                 "https://example.org/source")
            self.assertEqual(db.find_epa_toxval_records("Methane")["total"], 0)

    def test_mysql_parser_preserves_escaped_strings_nulls_and_decimal_text(self):
        parsed = parse_mysql_values(
            "INSERT INTO `toxval` VALUES (1,'quoted\\' text','line\\nnext',NULL,0.003);"
        )
        self.assertEqual(parsed, [(1, "quoted' text", "line\nnext", None, Decimal("0.003"))])

    def test_assertion_keeps_epa_link_but_marks_unmatched_local_identity(self):
        record = _assertion_tuple(
            self.release_id,
            {"toxval_id": 7, "chemical_id": "ToxVal00001", "species_id": None,
             "toxval_numeric": Decimal("0.1")},
            {"ToxVal00001": (42, "unmatched")}, {}, {},
        )
        # Position 5 is the EPA identity foreign key; position 6 is the local
        # identity status. EPA data remains searchable despite no local match.
        self.assertEqual(record[5:7], (42, "unmatched"))


if __name__ == "__main__":
    unittest.main()
