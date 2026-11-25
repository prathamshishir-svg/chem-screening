import unittest

from evidence_display import (
    classify_reported_yield,
    deduplicate_hazard_statements,
    describe_recorded_input_roles,
    recorded_reaction_display,
    recorded_source_label,
)


class EvidenceDisplayTests(unittest.TestCase):
    def test_yield_above_100_percent_is_anomalous_and_raw_value_is_preserved(self):
        for raw in ("100.5%", "104.7%", "122.0%"):
            with self.subTest(raw=raw):
                result = classify_reported_yield(raw)
                self.assertEqual(result["status"], "anomalous")
                self.assertEqual(result["raw"], raw)
                self.assertIn(raw, result["message"])
                self.assertIn("not treated as a valid yield", result["message"])

    def test_negative_yield_is_anomalous_and_boundaries_are_reported(self):
        self.assertEqual(classify_reported_yield("-0.1%")["status"], "anomalous")
        self.assertEqual(classify_reported_yield("0%")["status"], "reported")
        self.assertEqual(classify_reported_yield("100%")["status"], "reported")

    def test_unparseable_source_yield_is_not_presented_as_valid(self):
        result = classify_reported_yield("not stated")
        self.assertEqual(result["status"], "unvalidated")
        self.assertEqual(result["raw"], "not stated")
        self.assertIn("not treated as a validated yield", result["message"])
        self.assertEqual(classify_reported_yield(None)["status"], "missing")

    def test_duplicate_hazards_merge_text_and_keep_all_provenance(self):
        evidence = deduplicate_hazard_statements([
            "H300: Fatal if swallowed",
            "  H300:   Fatal if swallowed ",
            {"statement": "h300: fatal if swallowed", "source": "EPA source record"},
            {"text": "H315: Causes skin irritation", "sources": ["PubChem", "EPA"],
             "provenance": "EPA source record"},
        ])

        self.assertEqual(len(evidence), 2)
        self.assertEqual(evidence[0]["statement"], "H300: Fatal if swallowed")
        self.assertEqual(evidence[0]["sources"], [
            "PubChem PUG-View GHS Classification", "EPA source record",
        ])
        self.assertEqual(evidence[1]["sources"], ["PubChem", "EPA", "EPA source record"])

    def test_role_labels_distinguish_direct_reactant_evidence_from_cooccurrence(self):
        direct = describe_recorded_input_roles("reactant-reactant")
        mixed = describe_recorded_input_roles("reactant-reagent")
        other = describe_recorded_input_roles("other-participant-roles")

        self.assertIn("Both inputs are explicitly listed as reactants", direct)
        self.assertIn("does not establish", direct)
        self.assertIn("reagent may not be consumed", mixed)
        self.assertIn("not direct reactant-reactant evidence", other)
        self.assertIn("Co-occurrence alone", describe_recorded_input_roles("role-not-resolved"))

    def test_patent_and_ord_sources_have_distinct_labels(self):
        patent = recorded_source_label("USPTO")
        ord_source = recorded_source_label("ORD-mined")
        self.assertIn("USPTO patent-extracted", patent)
        self.assertIn("Open Reaction Database (ORD)", ord_source)
        self.assertNotEqual(patent, ord_source)

    def test_display_metadata_does_not_rewrite_curated_zinc_acetic_equation(self):
        equation = "Zn + 2 CH3COOH -> Zn(CH3COO)2 + H2"
        curated_record = {
            "source": "curated",
            "title": "Acid-metal reaction producing hydrogen gas",
            "equation": equation,
            "yield": "125%",
        }

        displayed = recorded_reaction_display(curated_record, 1)

        self.assertEqual(curated_record["equation"], equation)
        self.assertEqual(displayed["yield"]["status"], "anomalous")
        self.assertEqual(displayed["title"], "curated recorded reaction 1 [anomalous source yield]")


if __name__ == "__main__":
    unittest.main()
