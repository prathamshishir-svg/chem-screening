import json
import unittest
from unittest.mock import MagicMock, patch

import ai_explain
import bridge
import db
import lookup
import timing


ANSWER = {
    "summary": "Test summary",
    "reactions": [],
    "overall_hazards": [],
    "precautions": [],
    "confidence": "low",
    "uncertainties": [],
}


class TimingDiagnosticTests(unittest.TestCase):
    def test_duration_uses_monotonic_clock_and_logs_clear_label(self):
        with patch("timing.time.perf_counter", side_effect=[10.0, 11.25]), \
                patch("builtins.print") as output:
            started = timing.start_timer()
            timing.log_duration("mock operation", started)
        output.assert_called_once_with("[timing] mock operation: 1.250s", flush=True)

    def test_timing_failures_do_not_raise(self):
        with patch("timing.time.perf_counter", side_effect=OSError("clock unavailable")):
            started = timing.start_timer()
            self.assertIsNone(started)
            timing.log_duration("mock operation", started)
        with patch("timing.time.perf_counter", side_effect=[1.0, 2.0]), \
                patch("builtins.print", side_effect=OSError("terminal unavailable")):
            timing.log_duration("mock operation", 1.0)

    def test_gemini_request_timing_wraps_mocked_generate_content(self):
        response = MagicMock(text=json.dumps(ANSWER))
        with patch("google.genai.Client") as client_class, \
                patch("timing.start_timer", return_value=7.0), \
                patch("timing.log_duration") as log_duration:
            client_class.return_value.models.generate_content.return_value = response
            result = ai_explain._call_model("prompt remains unlogged", "model-id", "test-key")
        self.assertEqual(result, json.dumps(ANSWER))
        log_duration.assert_called_once_with("Gemini API request", 7.0)

    def test_uncached_pubchem_identity_and_structure_calls_are_timed(self):
        with patch.object(lookup, "_lookup_chemical", return_value={"cid": 1}), \
                patch("timing.start_timer", return_value=2.0), \
                patch("timing.log_duration") as log_duration:
            self.assertEqual(lookup.lookup_chemical("input is not logged"), {"cid": 1})
        log_duration.assert_called_once_with("PubChem name lookup (inclusive of GHS)", 2.0)

        with patch.object(bridge, "_get_smiles", return_value="CCO"), \
                patch("timing.start_timer", return_value=3.0), \
                patch("timing.log_duration") as log_duration:
            self.assertEqual(bridge.get_smiles(42), "CCO")
        log_duration.assert_called_once_with("PubChem structure lookup", 3.0)

    def test_local_database_lookup_timing_uses_mocked_cache(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = None
        with patch.object(db, "ensure_extra_tables"), \
                patch.object(db, "connect", return_value=connection), \
                patch.object(db, "lookup_chemical", return_value=None), \
                patch("timing.start_timer", return_value=4.0), \
                patch("timing.log_duration") as log_duration:
            self.assertIsNone(db.get_chemical("chemical name is not logged"))
        log_duration.assert_called_once_with("Local chemical database lookup", 4.0)

    def test_retry_wait_timing_with_mocked_gemini_errors(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = None
        with patch.object(ai_explain, "ai_ready", return_value=(True, "")), \
                patch.object(ai_explain, "_db", return_value=connection), \
                patch.object(ai_explain, "get_api_key", return_value="test-key"), \
                patch.object(ai_explain, "models_to_try", return_value=["model-id"]), \
                patch.object(ai_explain, "_call_model", side_effect=[
                    RuntimeError("429 busy"), json.dumps(ANSWER)]), \
                patch("ai_explain.time.sleep") as sleep, \
                patch("timing.log_duration") as log_duration:
            answer, info = ai_explain.get_ai_analysis(
                {"name": "A"}, {"name": "B"}, {})
        self.assertIsNotNone(answer)
        self.assertFalse(info["cached"])
        sleep.assert_called_once_with(6)
        self.assertTrue(any(call.args[0] == "Gemini retry wait"
                            for call in log_duration.call_args_list))

    def test_fallback_model_elapsed_timing_with_mocked_calls(self):
        connection = MagicMock()
        connection.execute.return_value.fetchone.return_value = None
        with patch.object(ai_explain, "ai_ready", return_value=(True, "")), \
                patch.object(ai_explain, "_db", return_value=connection), \
                patch.object(ai_explain, "get_api_key", return_value="test-key"), \
                patch.object(ai_explain, "models_to_try", return_value=["first", "fallback"]), \
                patch.object(ai_explain, "_call_model", side_effect=[
                    RuntimeError("404 model not found"), json.dumps(ANSWER)]), \
                patch("timing.start_timer", return_value=8.0), \
                patch("timing.log_duration") as log_duration:
            answer, info = ai_explain.get_ai_analysis(
                {"name": "A"}, {"name": "B"}, {})
        self.assertIsNotNone(answer)
        self.assertEqual(info["model"], "fallback")
        self.assertIn("Gemini fallback model elapsed (inclusive)",
                      [call.args[0] for call in log_duration.call_args_list])


if __name__ == "__main__":
    unittest.main()
