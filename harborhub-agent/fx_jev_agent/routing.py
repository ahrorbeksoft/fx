"""Hosted adapter for the same frozen policy as jev-fx-lab/src/router.mjs."""
from __future__ import annotations

import hashlib
import json
import math
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


def choice(answer: dict, allowed: list[str]) -> dict:
    if answer.get("type") != "choice" or answer.get("choice") not in allowed:
        raise ValueError("invalid_choice")
    probabilities = answer.get("probabilities", {})
    if set(probabilities) != set(allowed):
        raise ValueError("invalid_labels")
    if any(isinstance(p, bool) or not isinstance(p, (int, float)) or not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
        raise ValueError("invalid_probability")
    if abs(sum(probabilities.values()) - 1) > 0.03:
        raise ValueError("invalid_probability_sum")
    return {"label": answer["choice"], "probability": probabilities[answer["choice"]], "probabilities": probabilities}


def select(classification: dict | None, policy: dict = POLICY, requirements: dict | None = None) -> dict:
    requirements = requirements or {}
    eligible = {m["id"] for m in policy["models"] if m.get("enabled", True)
                and (not requirements.get("tools") or m.get("tools") is True)
                and (not requirements.get("vision") or m.get("vision") is True)
                and (not requirements.get("contextTokens") or m["contextWindow"] >= requirements["contextTokens"])
                and (not requirements.get("allowedModels") or m["id"] in requirements["allowedModels"])}
    explicit = requirements.get("explicitModel")
    if explicit:
        if explicit not in eligible:
            raise ValueError("explicit_model_ineligible")
        return {"model": explicit, "reason": "explicit_model"}
    fallback = policy["defaultModel"]
    if fallback not in eligible:
        raise ValueError("fallback_ineligible")
    if classification is None:
        return {"model": fallback, "reason": "classifier_unavailable"}
    c = classification.get("taskClass")
    family = classification["family"]
    if not c or c["probability"] < policy["minTaskClassProbability"] or family["probability"] < policy["minFamilyProbability"]:
        return {"model": fallback, "reason": "uncertain"}
    candidate = policy.get("familyTaskClassModels", {}).get(family["label"], {}).get(c["label"], policy["taskClassModels"].get(c["label"]))
    if candidate not in eligible:
        return {"model": fallback, "reason": "candidate_ineligible"}
    return {"model": candidate, "reason": f'family_{family["label"]}_task_{c["label"]}'}


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
    with urllib.request.build_opener(NoRedirect).open(request, timeout=15) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError("evaluation_response_too_large")
    result = json.loads(body)
    result["elapsedMs"] = (time.monotonic() - start) * 1000
    return result


def route(prompt: str) -> dict:
    requirements = {"tools": True, "contextTokens": 24000}
    initial = select(None, requirements=requirements)
    classification, error, usage, elapsed = None, None, None, None
    try:
        result = evaluate(prompt)
        usage, elapsed = result.get("usage"), result["elapsedMs"]
        classification = {"family": choice(result["answers"]["family"], list(QUESTIONS["family"]["criteria"])),
                          "taskClass": choice(result["answers"]["taskClass"], list(QUESTIONS["taskClass"]["criteria"])),
                          "usage": usage, "elapsedMs": elapsed, "taxonomyVersion": "2.0.0"}
    except Exception as exc:
        # Exception bodies can contain input/credentials; record only a type.
        error = "classifier_timeout" if isinstance(exc, TimeoutError) else "classifier_error"
    decision = select(classification, requirements=requirements) if classification else initial
    return {**decision, "classification": classification, "error": error, "usage": usage, "elapsedMs": elapsed,
            "policyHash": hashlib.sha256(json.dumps(POLICY, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
            "promptSha256": hashlib.sha256(prompt.encode()).hexdigest()}
