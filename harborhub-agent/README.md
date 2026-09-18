# Jev / fx hosted benchmark agent

This ACP wrapper runs the bundled, checksum-verified Linux x86_64 `fx ask` binary. It is intended for isolated Harbor task sandboxes. It grants fx full tool access inside that sandbox and pins reasoning effort to `high` in every arm.

`FX_EXPERIMENT_JEV_ROUTING=1` classifies the initial task with TypeSafe Jev and applies `config/policy.json`. The model stays fixed for the session, including resumed prompts. The routing policy is an experimental requirements rubric, not a trained success predictor. Family labels use taxonomy v2 from the earlier task-family classifier work. Family classification confidence is not a model-success probability.

`FX_EXPERIMENT_JEV_COMPACTION=1` enables the native extractive fast path. Both variables default off. Automatic compaction triggers remain unchanged. Jev decides which older tool results to keep; original user text, recent exchanges and stored source artifacts remain available. Compaction uses the existing transactional handoff. Excessive input, insufficient reduction, invalid replies, provider failures and oversized handoffs fall back to fx summarization. There is no measured speed or quality claim yet.

Use hosted `credential_mode: direct` and select the existing `AI_GATEWAY_API_KEY` secret. Typed evaluation uses the official AI Gateway endpoint. This adapter deliberately rejects missing direct credentials; a hosted proxy token must not be sent to the Gateway endpoint.

Harbor custom agents are resolved from a GitHub commit containing `harbor-agent.json`, this locked Python project and the pinned binary. No upload or launch is implicit in running local tests.

## Evidence

Per-trial artifacts:

- `fx.json`: final fx JSON envelope, including session usage.
- `fx-usage.json`: persisted native billing snapshot, with completeness and cache-token accounting.
- `fx-stderr.log`: stderr.
- `fx-trace.log`: generation, compaction and Jev evaluation events, including evaluation token usage.
- `jev-routing.json`: selected model, policy hash, probabilities, classifier latency, usage and fallback reason.
- `jev-telemetry.json`: feature flags and activation counts. A flag alone does not establish that Jev ran.

Jev evaluation billing does not currently provide fx's generation identity. The native ledger marks those invocations incomplete. Combine Jev token counts with the pinned model catalog when estimating cost, and retain the distinction between estimated and reconciled billing. Failed requests can be billed even without returned usage.

## Local checks

```sh
uv sync --locked
.venv/bin/python -m unittest discover -s tests -v
zig build test -Doptimize=ReleaseSafe -Dtest-filter=Jev
bun test tests/e2e/compaction-policy.test.ts
```

The final two commands run from the repository root. `compaction-policy.test.ts` is already a verification-only PGSO owner; the Jev tests inherit that classification.

To rebuild the bundled Linux binary from the repository root:

```sh
zig build -Dtarget=x86_64-linux-musl -Doptimize=ReleaseSafe --prefix zig-out-harbor
cp zig-out-harbor/bin/fx harborhub-agent/bin/fx
shasum -a 256 harborhub-agent/bin/fx | cut -d ' ' -f 1 > harborhub-agent/bin/fx.sha256
```

Record the resulting commit, binary hash, dataset content hash and frozen policy with each job. Full CI across all four native platforms remains required before this implementation can be considered ready to merge.
