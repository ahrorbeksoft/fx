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


class EvaluationCredentialTests(unittest.TestCase):
    def test_only_dedicated_key_is_sent_to_evaluator(self):
        for team in [None, "personal-team"]:
            env = {"AI_GATEWAY_API_KEY": "regular-inference-key",
                   "FX_JEV_GATEWAY_API_KEY": "personal-evaluation-key"}
            if team:
                env["FX_JEV_GATEWAY_TEAM"] = team
            with self.subTest(team=team), patch.dict(os.environ, env, clear=True):
                with patch("urllib.request.build_opener") as opener:
                    opener.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"answers":{}}'
                    evaluate("synthetic state", {})
                    request = opener.return_value.open.call_args.args[0]
                    self.assertEqual(request.full_url, EVALUATION_URL)
                    headers = {k.lower(): v for k, v in request.header_items()}
                    self.assertEqual(headers["authorization"], "Bearer personal-evaluation-key")
                    self.assertEqual(headers.get("x-vercel-ai-gateway-team"), team)
                    self.assertNotIn("regular-inference-key", str(headers))
                    self.assertEqual(os.environ["AI_GATEWAY_API_KEY"], "regular-inference-key")

    def test_missing_or_empty_evaluation_key_never_uses_inference_key(self):
        for dedicated in [None, ""]:
            env = {"AI_GATEWAY_API_KEY": "regular-inference-key"}
            if dedicated is not None:
                env["FX_JEV_GATEWAY_API_KEY"] = dedicated
            with self.subTest(dedicated=dedicated), patch.dict(os.environ, env, clear=True):
                with patch("urllib.request.build_opener") as opener:
                    with self.assertRaisesRegex(ValueError, "evaluation_gateway_credential_missing"):
                        evaluate("synthetic state", {})
                    opener.assert_not_called()


class EvaluationErrorTests(unittest.TestCase):
    def assert_denial(self, body, code):
        error = urllib.error.HTTPError(
            EVALUATION_URL, 403, "sensitive provider message", {}, io.BytesIO(body)
        )
        with patch.dict(os.environ, {"FX_JEV_GATEWAY_API_KEY": "test-secret"}, clear=True):
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
