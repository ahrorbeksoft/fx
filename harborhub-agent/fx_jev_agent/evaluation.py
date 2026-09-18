"""Credentialed Jev preflight transport. Model routing runs inside native fx."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "config"
POLICY = json.loads((CONFIG / "policy.json").read_text())
QUESTIONS = json.loads((CONFIG / "questions.json").read_text())
EVALUATION_URL = "https://ai-gateway.vercel.sh/v4/ai/evaluation-model"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def evaluate(state: str, questions: dict = QUESTIONS) -> dict:
    if not state or len(state.encode("utf-16-le")) // 2 > 24000:
        raise ValueError("classifier_input_size")
    # Direct mode is deliberate: the hosted generic inference proxy may not
    # implement Gateway's typed evaluation protocol. Never send its proxy token
    # to a provider. Preflight fails when the actual Gateway key is unavailable.
    key = os.environ.get("AI_GATEWAY_API_KEY")
    if not key:
        raise ValueError("direct_gateway_credential_missing")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "ai-gateway-protocol-version": "0.0.1", "ai-evaluation-model-specification-version": "4", "ai-model-id": "typesafe-ai/jev"}
    if team := os.environ.get("FX_JEV_GATEWAY_TEAM"):
        headers["x-vercel-ai-gateway-team"] = team
    request = urllib.request.Request(EVALUATION_URL, data=json.dumps({"state": state, "questions": questions, "providerOptions": {"gateway": {"zeroDataRetention": True}}}).encode(), headers=headers, method="POST")
    start = time.monotonic()
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
            body = response.read(2_000_001)
    except urllib.error.HTTPError as error:
        # Keep only known machine error codes; never echo provider error text.
        code = "unknown"
        try:
            detail = json.loads(error.read(4096))
            nested = detail.get("error", detail) if isinstance(detail, dict) else None
            candidate = nested.get("code") if isinstance(nested, dict) else None
            if candidate in {"no_providers_available", "permission_denied", "unauthorized", "model_not_found", "rate_limit_exceeded"}:
                code = candidate
        except (ValueError, TypeError):
            pass
        raise RuntimeError(f"Gateway evaluation denied: HTTP {error.code}, code={code}") from None
    if len(body) > 2_000_000:
        raise ValueError("evaluation_response_too_large")
    result = json.loads(body)
    result["elapsedMs"] = (time.monotonic() - start) * 1000
    return result
