import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import vm from 'node:vm';

test('portal initializes the API client before loading model readiness and profiles', async () => {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', innerHTML: '', dataset: {}, disabled: false,
      addEventListener() {}, querySelector() { return {textContent: ''}; },
      appendChild(option) { if (option.selected) this.value = option.value; },
    });
    return elements.get(id);
  }
  const calls = [];
  const context = vm.createContext({
    document: {getElementById: element, querySelector() { return null; },
      createElement() { return {}; }},
    localStorage: {getItem() { return null; }},
    sessionStorage: {getItem() { return null; }},
    Response,
    fetch: async url => {
      calls.push(url);
      const data = url.endsWith('/info')
        ? {backend_id: 'local', capabilities: {model_profiles: true, byok: true, model_config_mode: 'managed'}}
        : {profiles: [{id: 'test-profile', label: 'Test', model: 'test-model', configured: true}], default_profile: 'test-profile'};
      return new Response(JSON.stringify({ok: true, data}), {headers: {'Content-Type': 'application/json'}});
    },
  });
  context.window = context;
  for (const name of ['agent-client.js', 'chat.js']) {
    vm.runInContext(await readFile(new URL(`../../app/web/${name}`, import.meta.url), 'utf8'), context, {filename: name});
  }
  await context.refreshModelReadiness();
  await context.loadModelProfiles();
  assert.equal(typeof context.agentClient.info, 'function');
  assert.ok(calls.includes('/api/v1/agent/info'));
  assert.ok(calls.includes('/api/v1/agent/model-profiles'));
  assert.equal(element('model-readiness').dataset.state, 'configured');
  assert.equal(element('model-profile').value, 'test-profile');
  assert.doesNotMatch(element('model-profile-detail').textContent, /ReferenceError|无法读取/);
});
