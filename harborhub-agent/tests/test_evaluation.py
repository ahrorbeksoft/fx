import io
import json
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fx_jev_agent.evaluation import EVALUATION_URL, evaluate


class EvaluationErrorTests(unittest.TestCase):
    def assert_denial(self, body, code):
        error = urllib.error.HTTPError(
            EVALUATION_URL, 403, "sensitive provider message", {}, io.BytesIO(body)
        )
        with patch.dict(os.environ, {"AI_GATEWAY_API_KEY": "test-secret"}, clear=True):
            with patch("urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(RuntimeError) as raised:
                    evaluate("synthetic state", {})
        self.assertEqual(
            str(raised.exception),
            f"Gateway evaluation denied: HTTP 403, code={code}",
        )
        self.assertTrue(raised.exception.__suppress_context__)

    def test_documented_provider_allowlist_denial(self):
        self.assert_denial(json.dumps({
            "error": "Your team has restricted access to this provider.",
            "type": "no_providers_available", "statusCode": 403,
        }).encode(), "no_providers_available")

    def test_nested_and_top_level_machine_codes(self):
        for envelope in [
            {"error": {"code": "permission_denied"}},
            {"error": {"type": "permission_denied"}},
            {"code": "permission_denied"},
        ]:
            with self.subTest(envelope=envelope):
                self.assert_denial(json.dumps(envelope).encode(), "permission_denied")

    def test_unknown_or_malformed_errors_never_expose_provider_text(self):
        for body in [
            b'<html>test-secret</html>', b'null', b'[]', b'"test-secret"',
            b'{"error":{"code":"test-secret","message":"test-secret"}}',
            b'{"error":{"type":[],"code":{}}}',
            b'{"error":"test-secret","type":"test-secret"}',
            b'{"error":"' + b'x' * 5000 + b'"}',
        ]:
            with self.subTest(body=body[:80]):
                self.assert_denial(body, "unknown")


if __name__ == "__main__":
    unittest.main()
