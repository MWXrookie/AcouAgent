/* AcouAgent browser-neutral Agent API client. No framework or build step. */
(function(root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.AcouAgentApi = factory();
  }
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';

  class AgentApiError extends Error {
    constructor(code, message, options) {
      super(message || code || 'agent_api_error');
      this.name = 'AgentApiError';
      this.code = code || 'agent_api_error';
      this.status = (options && options.status) || 0;
      this.requestId = (options && options.requestId) || null;
      this.details = options && options.details;
    }
  }

  function defaultBaseUrl() {
    if (typeof document !== 'undefined') {
      var meta = document.querySelector('meta[name="acouagent-api-base"]');
      if (meta && meta.content) return meta.content.replace(/\/+$/, '');
    }
    return '/api/v1/agent';
  }

  function joinUrl(base, path) {
    return String(base || '').replace(/\/+$/, '') + '/' + String(path || '').replace(/^\/+/, '');
  }

  function errorForPayload(payload, status) {
    var error = payload && payload.error ? payload.error : {};
    return new AgentApiError(
      error.code || 'agent_api_error',
      error.message || ('服务返回 HTTP ' + status),
      {status: status, requestId: error.request_id, details: error.details}
    );
  }

  function createAgentClient(options) {
    var config = options || {};
    var baseUrl = config.baseUrl || defaultBaseUrl();
    var fetchImpl = config.fetchImpl || (typeof fetch !== 'undefined' ? fetch.bind(globalThis) : null);
    if (!fetchImpl) throw new Error('fetch_unavailable');

    async function request(path, requestOptions) {
      var init = requestOptions || {};
      var response;
      try {
        response = await fetchImpl(joinUrl(baseUrl, path), init);
      } catch (error) {
        throw new AgentApiError('network_error', String(error && error.message || error));
      }
      var payload = null;
      try {
        payload = await response.json();
      } catch (_) {
        throw new AgentApiError('invalid_response', '服务返回了无效 JSON。', {status: response.status});
      }
      if (!response.ok || !payload || payload.ok !== true) {
        throw errorForPayload(payload, response.status);
      }
      return payload.data;
    }

    async function chat(body, handlers, requestOptions) {
      var callbacks = handlers || {};
      var init = Object.assign({
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/x-ndjson, application/json'},
        body: JSON.stringify(body || {})
      }, requestOptions || {});
      var response;
      try {
        response = await fetchImpl(joinUrl(baseUrl, '/chat'), init);
      } catch (error) {
        throw new AgentApiError('network_error', String(error && error.message || error));
      }
      if (!response.ok) {
        var errorPayload = null;
        try { errorPayload = await response.json(); } catch (_) {}
        throw errorForPayload(errorPayload, response.status);
      }
      var contentType = String(response.headers.get('content-type') || '');
      if (!contentType.includes('application/x-ndjson')) {
        var payload = await response.json();
        if (!payload || payload.ok !== true) throw errorForPayload(payload, response.status);
        var result = payload.data;
        if (result && callbacks.onResult) callbacks.onResult(result);
        return result;
      }
      if (!response.body || !response.body.getReader) {
        throw new AgentApiError('stream_unavailable', '浏览器不支持流式响应。');
      }
      var reader = response.body.getReader();
      var decoder = new TextDecoder();
      var pending = '';
      var terminal = null;
      while (true) {
        var chunk = await reader.read();
        pending += decoder.decode(chunk.value || new Uint8Array(), {stream: !chunk.done});
        var lines = pending.split('\n');
        pending = lines.pop();
        for (var i = 0; i < lines.length; i++) {
          var line = lines[i].trim();
          if (!line) continue;
          var event;
          try { event = JSON.parse(line); } catch (_) {
            throw new AgentApiError('invalid_event', '工作流事件 JSON 无效。');
          }
          if (!event || !event.event) throw new AgentApiError('invalid_event', '工作流事件缺少 event。');
          if (event.event === 'progress' && callbacks.onProgress) callbacks.onProgress(event);
          else if (event.event === 'skill_usage' && callbacks.onSkillUsage) callbacks.onSkillUsage(event);
          else if (event.event === 'result') {
            terminal = event;
            if (callbacks.onResult) callbacks.onResult(event);
          }
        }
        if (chunk.done) break;
      }
      if (pending.trim()) {
        try { terminal = JSON.parse(pending.trim()); } catch (_) { /* incomplete line is rejected below */ }
      }
      if (!terminal || terminal.event !== 'result') {
        throw new AgentApiError('workflow_incomplete', '连接中断，未收到完整结果。');
      }
      return terminal;
    }

    return {
      baseUrl: baseUrl,
      info: function() { return request('/info', {headers: {'Accept': 'application/json'}}); },
      chat: chat,
      listModelProfiles: function() {
        return request('/model-profiles', {headers: {'Accept': 'application/json'}});
      },
      createModelSession: function(body) {
        return request('/model-sessions', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body || {})
        });
      },
      testModelProfile: function(profileId) {
        return request('/model-profiles/' + encodeURIComponent(profileId) + '/test', {method: 'POST'});
      },
      deleteModelSession: function(profileId) {
        return request('/model-sessions/' + encodeURIComponent(profileId), {method: 'DELETE'});
      }
    };
  }

  return {AgentApiError: AgentApiError, createAgentClient: createAgentClient};
});
