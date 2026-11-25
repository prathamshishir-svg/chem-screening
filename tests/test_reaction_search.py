import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import db
from chemkeys import split_source_references


class ReactionSearchValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "reactions.db"
        con = sqlite3.connect(self.db_path)
        con.executescript(
            "CREATE TABLE rxn_records (id INTEGER PRIMARY KEY, reactants TEXT, reagents TEXT, "
            "products TEXT, year INTEGER, patent TEXT, yield TEXT, source TEXT, n_mols INTEGER);"
            "CREATE TABLE rxn_molecules (rxn_id INTEGER, smiles TEXT, role TEXT);"
            "CREATE TABLE ord_reaction_records (id INTEGER PRIMARY KEY, rxn_id INTEGER, "
            "source_record_id TEXT, dataset_id TEXT, source_doi TEXT, is_mined INTEGER, identity_status TEXT);"
            "CREATE TABLE ord_reaction_participants (ord_record_id INTEGER, standardized_smiles TEXT, "
            "source_role TEXT, side TEXT);"
            "CREATE INDEX idx_rxnmol_smiles ON rxn_molecules(smiles);"
            "INSERT INTO rxn_records VALUES (1,'CCO.CC(=O)O','', 'CCOC(C)=O',2020,'US1','', 'USPTO',3);"
            "INSERT INTO rxn_molecules VALUES (1,'CCO','R');"
            "INSERT INTO rxn_molecules VALUES (1,'CCO','R');"
            "INSERT INTO rxn_molecules VALUES (1,'CC(=O)O','G');"
            "INSERT INTO rxn_records VALUES (2,'CCO.CC(=O)O','', 'CCOC(C)=O',2021,'',NULL,'ORD-mined',3);"
            "INSERT INTO rxn_molecules VALUES (2,'CCO','R');"
            "INSERT INTO rxn_molecules VALUES (2,'CC(=O)O','R');"
            "INSERT INTO ord_reaction_records VALUES (10,2,'ord-record-2','dataset-1','10.1234/example',1,'new_unique');"
            "INSERT INTO ord_reaction_participants VALUES (10,'CCO','REACTANT','input');"
            "INSERT INTO ord_reaction_participants VALUES (10,'CC(=O)O','REACTANT','input');"
            "INSERT INTO rxn_records VALUES (3,'CCO','', 'CC=O',2019,'US2','', 'USPTO',2);"
            "INSERT INTO rxn_molecules VALUES (3,'CCO','R');"
        )
        con.commit()
        con.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _search(self, first="CCO", second="CC(=O)O", limit=5):
        with patch.object(db, "DB_FILE", str(self.db_path)):
            return db.find_recorded(first, second, limit)

    def test_both_valid_structures_return_distinct_matches_with_roles_and_source_ids(self):
        records, total = self._search()
        self.assertEqual(total, 2)
        self.assertEqual({row["source"] for row in records}, {"USPTO", "ORD-mined"})
        by_source = {row["source"]: row for row in records}
        self.assertEqual(by_source["USPTO"]["evidence_relation"], "reactant-reagent")
        self.assertEqual(by_source["USPTO"]["input_roles"], [["reactant"], ["reagent"]])
        self.assertEqual(by_source["ORD-mined"]["source_record_id"], "ord-record-2")
        self.assertEqual(by_source["ORD-mined"]["evidence_relation"], "reactant-reactant")
        # The two source records have the same reaction strings, but represent
        # separately attributed records and must not be silently merged.
        self.assertEqual(len({row["source"] for row in records}), 2)

    def test_source_aware_allocation_includes_ord_even_when_patent_is_ranked_first(self):
        records, total = self._search(limit=2)
        self.assertEqual(total, 2)
        self.assertEqual([row["source"] for row in records], ["USPTO", "ORD-mined"])

    def test_non_doi_ord_identifier_is_not_labelled_as_a_doi(self):
        dois, other = split_source_references("10.1234/valid-doi;1;0;.;garbled-id")
        self.assertEqual(dois, ["10.1234/valid-doi"])
        self.assertEqual(other, ["1", "0", ".", "garbled-id"])

    def test_missing_first_or_second_structure_never_degrades_to_one_sided_matches(self):
        for first, second, label in ((None, "CC(=O)O", "Chemical 1"),
                                     ("CCO", None, "Chemical 2")):
            with self.subTest(label=label), self.assertRaises(db.ReactionSearchError) as error:
                self._search(first, second)
            self.assertEqual(error.exception.input_label, label)

    def test_invalid_smiles_or_rdkit_parse_failure_never_degrades_to_one_sided_matches(self):
        for first, second, label in (("not a smiles", "CC(=O)O", "Chemical 1"),
                                     ("CCO", "FCl(F)F", "Chemical 2")):
            with self.subTest(label=label), self.assertRaises(db.ReactionSearchError) as error:
                self._search(first, second)
            self.assertEqual(error.exception.input_label, label)

    def test_genuine_zero_match_is_not_a_structure_error(self):
        records, total = self._search("[He]", "CC(=O)O")
        self.assertEqual(records, [])
        self.assertEqual(total, 0)


if __name__ == "__main__":
    unittest.main()
