import unittest

from wcpredict.ai_copilot import explain_context


class AiCopilotTests(unittest.TestCase):
    def test_missing_key_disables_adapter_before_transport(self):
        calls = []
        result = explain_context({"probabilities": {"home": 0.5}}, api_key=None, transport=lambda *args: calls.append(args))
        self.assertEqual("disabled", result.status)
        self.assertEqual([], calls)

    def test_ai_response_cannot_replace_deterministic_probabilities(self):
        def transport(_url, _headers, payload, _timeout):
            self.assertIn("responses", _url)
            self.assertIn("probabilities", payload["input"])
            return {"output_text": '{"narrative":"La baja aumenta la incertidumbre.","flags":["lineup"],"probabilities":{"home":0.99}}'}

        result = explain_context(
            {"probabilities": {"home": 0.5, "draw": 0.3, "away": 0.2}},
            api_key="test-key",
            transport=transport,
        )
        self.assertEqual("ready", result.status)
        self.assertEqual("La baja aumenta la incertidumbre.", result.narrative)
        self.assertFalse(hasattr(result, "probabilities"))


if __name__ == "__main__":
    unittest.main()
