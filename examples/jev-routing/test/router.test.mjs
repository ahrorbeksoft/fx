import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { route, selectModel } from '../src/router.mjs';
import { choice, evaluate } from '../src/gateway.mjs';
const policy = JSON.parse(readFileSync(new URL('../config/policy.json', import.meta.url)));
const classified = { family: { label: "code-generation", probability: 0.9 }, taskClass: { label: 'routine', probability: 0.9 } };

test('explicit choice bypasses classification and preserves model', async () => {
  const d = await route('test', policy, { explicitModel: policy.defaultModel }, { classify: () => { throw new Error('must not call'); } });
  assert.equal(d.model, policy.defaultModel); assert.equal(d.reason, 'explicit_model');
});
test('timeouts retain default and expose fallback', async () => {
  const d = await route('test', policy, {}, { classify: async () => { throw new DOMException('timeout', 'TimeoutError'); } });
  assert.equal(d.model, policy.defaultModel); assert.equal(d.error, 'classifier_timeout');
});
test('uncertainty and missing capabilities cannot cause a downgrade', () => {
  assert.equal(selectModel({ ...classified, family: { probability: 0.2 } }, policy).model, policy.defaultModel);
  assert.throws(() => selectModel(classified, { ...policy, models: policy.models.map(m => ({ ...m, vision: false })) }, { vision: true }), /No eligible/);
  assert.equal(selectModel(classified, policy, { allowedModels: [policy.defaultModel] }).reason, 'candidate_ineligible');
});
test('a confident eligible choice uses configured policy, never a model name invented by Jev', () => {
  assert.equal(selectModel(classified, policy).model, policy.taskClassModels.routine);
  assert.throws(() => choice({ type: 'choice', choice: 'invented', probabilities: { invented: 1 } }, ['routine']));
  assert.throws(() => choice({ type: 'choice', choice: 'routine', probabilities: { routine: NaN } }, ['routine']));
});
test('provider error text cannot leak prompt or credential into diagnostics', async () => {
  await assert.rejects(evaluate('private', {}, { apiKey: 'test-key', fetch: async () => new Response('private test-key', { status: 403 }) }), e => !e.message.includes('private') && !e.message.includes('test-key'));
});
