import unittest
from unittest.mock import Mock

import bridge


class StructureResolutionTests(unittest.TestCase):
    def _response(self, status=200, payload=None):
        response = Mock(status_code=status)
        response.json.return_value = payload
        return response

    def test_valid_pubchem_structure_records_source_identifier_and_retrieval_time(self):
        payload = {"PropertyTable": {"Properties": [{
            "CID": 6338, "CanonicalSMILES": "C=CCl", "IsomericSMILES": "C=CCl",
        }]}}
        result = bridge._get_structure(6338, http_get=Mock(return_value=self._response(payload=payload)))
        self.assertEqual(result["smiles"], "C=CCl")
        self.assertEqual(result["validation"], "rdkit_valid")
        self.assertEqual((result["source"], result["identifier_type"], result["identifier"]),
                         ("PubChem PUG REST", "PubChem CID", "6338"))
        self.assertTrue(result["retrieved_at"])

    def test_disconnected_pubchem_structure_is_rdkit_valid(self):
        payload = {"PropertyTable": {"Properties": [{
            "CanonicalSMILES": "C(=O)(O)[O-].[Na+]",
        }]}}
        result = bridge._get_structure(516892, http_get=Mock(return_value=self._response(payload=payload)))
        self.assertEqual(result["smiles"], "C(=O)(O)[O-].[Na+]")
        self.assertEqual(result["validation"], "rdkit_valid")

    def test_structure_rejected_by_rdkit_is_not_used_as_a_search_structure(self):
        payload = {"PropertyTable": {"Properties": [{"CanonicalSMILES": "FCl(F)F"}]}}
        result = bridge._get_structure(24637, http_get=Mock(return_value=self._response(payload=payload)))
        self.assertIsNone(result["smiles"])
        self.assertEqual(result["source_smiles"], "FCl(F)F")
        self.assertEqual(result["validation"], "rdkit_rejected_source_structure")

    def test_missing_malformed_timeout_and_unavailable_responses_are_reported(self):
        self.assertEqual(bridge._get_structure(1, http_get=Mock(return_value=self._response(404)))["error"],
                         "pubchem_http_404")
        malformed = self._response(payload={"unexpected": []})
        self.assertEqual(bridge._get_structure(1, http_get=Mock(return_value=malformed))["error"],
                         "missing_pubchem_structure")
        timed_out = Mock(side_effect=bridge.requests.Timeout())
        self.assertEqual(bridge._get_structure(1, http_get=timed_out)["error"], "pubchem_timeout")
        unavailable = Mock(side_effect=bridge.requests.ConnectionError())
        self.assertEqual(bridge._get_structure(1, http_get=unavailable)["error"],
                         "pubchem_network_error")


if __name__ == "__main__":
    unittest.main()
