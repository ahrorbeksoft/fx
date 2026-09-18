import { expect, test } from "bun:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

process.env.FX_E2E_DISABLE_DOTENV = "1";
const { fakeGatewayFinalText, startDynamicFakeGateway } = await import("./tmux-helpers");
const binary = resolve(import.meta.dir, "../../zig-out/bin/fx");
const cli = resolve(import.meta.dir, "../../examples/jev-routing/src/cli.mjs");

test("Jev routing launcher selects a model and runs the built fx binary", async () => {
  const root = mkdtempSync(join(tmpdir(), "fx-jev-routing-"));
  const home = join(root, "home"), cwd = join(root, "workspace");
  mkdirSync(join(home, ".fx"), { recursive: true }); mkdirSync(cwd);
  writeFileSync(join(home, ".fx/settings.json"), JSON.stringify({ auto_upgrade: false }));
  const selected = "openai/gpt-5.6-luna", bodies: string[] = [];
  const gateway = startDynamicFakeGateway(body => {
    bodies.push(body);
    return fakeGatewayFinalText("ROUTED_MODEL_REPLIED");
  }, { models: [{ id: selected, type: "language", tags: ["tool-use"], context_window: 1050000, max_tokens: 8192 }] });
  const trace = join(root, "decision.json"), evaluation = join(root, "evaluation.json");
  const preload = join(root, "evaluation-fixture.mjs");
  // The production client keeps its fixed URL. This process-local fixture
  // replaces fetch, so this deterministic test cannot call a real evaluator.
  writeFileSync(preload, `import { writeFileSync } from 'node:fs';
globalThis.fetch = async (url, options) => {
  if (url !== 'https://ai-gateway.vercel.sh/v4/ai/evaluation-model') throw new Error('Unexpected evaluation URL');
  const body = JSON.parse(options.body);
  writeFileSync(${JSON.stringify(evaluation)}, JSON.stringify(body));
  const choices = {family: 'code-generation', taskClass: 'routine'};
  return Response.json({answers: Object.fromEntries(Object.entries(body.questions).map(([key, question]) => [key, {
    type:'choice', choice:choices[key], probabilities:Object.fromEntries(Object.keys(question.criteria).map(label => [label, Number(label === choices[key])]))
  }])), usage:{inputTokens:100,outputTokens:1}});
};\n`);
  let passed = false;
  try {
    const prompt = "Create a small function that returns the sum of two integers.";
    const input = join(root, "prompt.txt"); writeFileSync(input, prompt);
    const child = Bun.spawn(["node", "--import", preload, cli, "run-fx", "--binary", binary, "--prompt-file", input, "--trace", trace], {
      cwd, env: {
        PATH: process.env.PATH ?? "/usr/bin:/bin", HOME: home, TMPDIR: root,
        AI_GATEWAY_API_KEY: "synthetic-jev-routing", FX_DISABLE_KEYCHAIN: "1", FX_E2E_DISABLE_DOTENV: "1",
        FX_AUTO_UPGRADE: "0", FX_SOUND: "0", FX_GATEWAY_BASE_URL: gateway.baseUrl,
        FX_GATEWAY_CHAT_URL: gateway.chatUrl, FX_E2E_GATEWAY_CHAT_URL: gateway.chatUrl,
        FX_E2E_GATEWAY_MODELS_URL: `${gateway.baseUrl}/coding-agent/v1/models`,
      }, stdin: "ignore", stdout: "pipe", stderr: "pipe",
    });
    const timeout = setTimeout(() => child.kill("SIGKILL"), 30_000);
    try {
      const [stdout, stderr, code] = await Promise.all([new Response(child.stdout).text(), new Response(child.stderr).text(), child.exited]);
      expect(code).toBe(0); expect(stderr).toBe("");
      expect(JSON.parse(stdout).output).toBe("ROUTED_MODEL_REPLIED");
      const decision = JSON.parse(readFileSync(trace, "utf8"));
      expect(decision.model).toBe(selected);
      expect(decision.error).toBeNull();
      expect(decision.reason).toBe("family_code-generation_task_routine");
      expect(JSON.parse(readFileSync(evaluation, "utf8")).state).toBe(prompt);
      expect(bodies).toHaveLength(1);
      expect(JSON.parse(stdout).model).toBe(selected);
      expect(bodies[0]).toContain(prompt);
      passed = true;
    } finally { clearTimeout(timeout); }
  } finally {
    gateway.stop();
    if (passed) rmSync(root, { recursive: true, force: true });
    else console.error(`Jev routing evidence: ${root}`);
  }
}, 45_000);
