import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const { AgentApiError, createAgentClient } = require('../../app/web/agent-client.js');

function jsonResponse(payload, status = 200, headers = {}) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {'Content-Type': 'application/json', ...headers},
  });
}

test('info returns the v1 data envelope and honors an explicit base URL', async () => {
  const calls = [];
  const client = createAgentClient({
    baseUrl: 'https://agent.example/api/v1/agent',
    fetchImpl: async (url, init) => {
      calls.push({url, init});
      return jsonResponse({ok: true, data: {backend_id: 'local', capabilities: {byok: true}}});
    },
  });
  const data = await client.info();
  assert.equal(data.backend_id, 'local');
  assert.equal(calls[0].url, 'https://agent.example/api/v1/agent/info');
});

test('chat parses chunked NDJSON and returns the terminal result', async () => {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('{"event":"progress","stage":"one"}\n{"event":"result",'));
      controller.enqueue(encoder.encode('"report":"ok","workflow_status":"succeeded"}\n'));
      controller.close();
    },
  });
  const progress = [];
  const client = createAgentClient({
    baseUrl: '/api/v1/agent',
    fetchImpl: async () => new Response(stream, {
      status: 200,
      headers: {'Content-Type': 'application/x-ndjson'},
    }),
  });
  const result = await client.chat(
    {message: 'test', stream: true},
    {onProgress: event => progress.push(event.stage)},
  );
  assert.deepEqual(progress, ['one']);
  assert.equal(result.report, 'ok');
});

test('API errors expose code, request id, and HTTP status', async () => {
  const client = createAgentClient({
    baseUrl: '/api/v1/agent',
    fetchImpl: async () => jsonResponse({
      ok: false,
      error: {code: 'capability_not_supported', message: 'unsupported', request_id: 'req-1'},
    }, 501),
  });
  await assert.rejects(
    () => client.listModelProfiles(),
    error => error instanceof AgentApiError
      && error.code === 'capability_not_supported'
      && error.requestId === 'req-1'
      && error.status === 501,
  );
});
