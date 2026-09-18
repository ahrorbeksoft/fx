# fx five-arm hosted benchmark adapter

This is benchmark infrastructure, kept on an experiment branch outside the two
Jev feature PRs. It adapts Harbor's Agent Client Protocol (ACP) to `fx ask`; it is
not another reasoning agent or a replacement for fx. Each isolated cloud task
gets one checksum-verified Linux binary, the same tool permissions and `high`
reasoning effort. The adapter forwards the task and collects artifacts.

The frozen matrix uses five builds listed in `config/builds.json`:

| Arm | Binary | Extra behavior |
|---|---|---|
| main | main | none |
| patch-retry | main + PRs #499 and #500 | `patch_v3` and `adaptive_v1` enabled |
| compaction | main + native Jev compaction | compaction enabled |
| routing | main + native Jev routing | routing at root prompt and child assignment boundaries |
| both | main + native Jev compaction + routing | both native features |

`FX_BENCH_VARIANT` selects a declared build. The adapter rejects switches that do
not match that build. Main is a clean source snapshot without either native
experiment. Every arm uses this same adapter revision. Routing runs inside fx:
`--model jev/auto` selects the root mode and
`FX_EXPERIMENT_JEV_SUBAGENT_ROUTING=1` enables independent child selection.
An explicit child model wins. A selected model stays fixed throughout its tool
loop; the next new prompt or idle child assignment may select another model.
The Python adapter only parses native decisions and transports Harbor prompts.

All arms run the same 89 Terminal-Bench 2.1 revision 6 tasks, one attempt per task:
445 trials. The native compaction trigger is unchanged. A task with no successful
Jev compaction does not provide evidence about compaction efficacy. The routing
policy is an experimental requirements rubric, not a learned success predictor.

Use hosted `credential_mode: direct` with the stored `AI_GATEWAY_API_KEY` selected.
The generic Harbor inference proxy is not assumed to support Gateway's typed
Jev evaluation protocol. A separate hello-world smoke gates live evaluation and
all three candidate models before a full matrix. Security-policy failures block
launch; no provider allowlist is changed by this adapter.

Harbor resolves the package and its locked Python runtime from a pinned GitHub
commit. Local tests do not upload or launch anything. The binary source commits
are independent of the adapter's source commit and are recorded per trial.

## Per-trial evidence

- `benchmark-build.json`: source commit, binary hash and build provenance.
- `fx.json`: final fx JSON envelope and token usage.
- `fx-usage.json`: native billing snapshot and completeness.
- `fx-stderr.log` and `fx-trace.log`: runtime output and feature activation.
- `jev-routing.json`: every native root and child decision, policy version,
  turn/child identity, selected model, probabilities, usage, latency and fallback.
- `jev-telemetry.json`: build variant, all feature switches, observed editor
  and retry flags, and actual Jev evaluation/compaction counts.

Terminal-Bench generally supplies an initial task prompt. Its score alone cannot
establish follow-up routing quality; child-routing evidence depends on actual
delegation. The analysis reports root and child counts separately and includes
trials where no child routed.

Separate main and routing diagnostic jobs set `FX_BENCH_MULTI_PROMPT_PROBE=1`.
They run three prompts in one fx session: implement a parser, follow up with
"Do it", then delegate a second change. Independent functional checks after
each prompt and native routing traces are saved in `multi-prompt-probe.json`
and the accompanying logs. This small probe is not part of the 445 scored
Terminal-Bench trials, and its quality result is separate from hello-world's
Harbor reward. The adapter does not infer diagnostic success from that reward.

Jev costs are estimates from returned input tokens and the dated catalog.
Evaluation calls lack fx's normal generation identity, so native billing can be
incomplete. Missing usage is unknown spend. No benchmark improvement is claimed
until the complete results have been checked against the pinned matrix.

## Local checks and rebuilding

```sh
uv sync --locked
.venv/bin/python -m unittest discover -s tests -v
```

Build each native source checkout separately with Zig 0.16.0:

```sh
zig build -Dtarget=x86_64-linux-musl -Doptimize=ReleaseSafe --prefix zig-out-harbor
```

Copy each artifact to the corresponding `bin/fx-VARIANT`, then record its SHA-256,
exact source commit, base commit, compiler version and build command in
`config/builds.json`. Never label a binary as main because its feature flags are
off: it must be built from the pinned main checkout. Full CI on each feature
commit is required before merge readiness.
