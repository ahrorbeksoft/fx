# Experimental Jev model routing

Classify the initial task with `typesafe-ai/jev` through AI Gateway, then select
an eligible model using a frozen, deterministic policy. The reusable JavaScript
API also works outside fx. The launcher invokes `fx ask --model` once and keeps
that model for the session. It does not add per-turn routing or a managed Gateway
router. Compaction and hosted benchmark infrastructure are separate experiments.

This is a policy hypothesis, not a trained predictor of model success. The
12-family taxonomy and a routine/general/demanding rubric select among Kimi K3,
GPT 5.6 Luna and GPT 5.6 Sol. Low confidence or evaluator failure retains Kimi K3.
Explicit model choices bypass evaluation. Model availability, tool/vision
requirements and context limits are enforced before selection.

Requires Node 22+ and `AI_GATEWAY_API_KEY` or `VERCEL_OIDC_TOKEN` in the environment.
The Gateway team must permit TypeSafe AI and each candidate model. No dependencies
or installation step are needed. The provider catalog is a dated snapshot, not
proof that the current account has access.

From this directory:

```sh
node --test test/*.test.mjs
printf 'Diagnose the deadlock in this scheduler.' | node src/cli.mjs route
printf 'Diagnose the deadlock in this scheduler.' | node src/cli.mjs run-fx --binary /absolute/path/to/fx/zig-out/bin/fx --trace /tmp/routing.json
```

`--model` supplies an explicit eligible model. `--policy` selects a policy file;
`--prompt-file` reads task text from a file. Otherwise input comes from stdin.
Routing decisions contain the policy hash, selected model, confidence and any
fallback. Provider errors are redacted. Evaluations use a fixed Gateway endpoint,
bounded responses, a 15-second deadline and zero-data-retention routing.

```js
import { route } from './src/router.mjs';
const decision = await route(task, policy, { tools: true, contextTokens: 24000 });
// Use decision.model with AI Gateway or an fx launcher.
```

No quality, latency or cost improvement has been established. Benchmark this
policy on held-out tasks and include classifier usage and fallback frequency.
