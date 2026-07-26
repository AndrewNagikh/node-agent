// Thin fetch wrappers over the real orchestrator + node_agent HTTP APIs.
// No mocking, no fixtures -- every call here hits a real endpoint that
// exists in tools/distributed/{orchestrator,node_agent}.cpp today.

async function getJson(url, opts) {
  const res = await fetch(url, opts);
  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text };
  }
  if (!res.ok) {
    const err = new Error(body.error || `HTTP ${res.status}`);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

async function postJson(url, payload) {
  return getJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload || {}),
  });
}

export function nodeAgentBase(node) {
  return `http://${node.host}:${node.port}`;
}

// --- orchestrator -----------------------------------------------------

export async function fetchNodes(orchestratorUrl) {
  const data = await getJson(`${orchestratorUrl}/nodes`);
  return data.nodes || [];
}

export async function fetchModels(orchestratorUrl) {
  return getJson(`${orchestratorUrl}/models`);
}

// Sampling defaults mirror the runtime's own: temp 0 means greedy (the
// deterministic argmax this cluster used before sampling was configurable),
// and the rest are the disabled-values for their samplers.
export const SAMPLING_DEFAULTS = {
  temp: 0,
  top_k: 1,
  top_p: 1,
  min_p: 0,
  repeat_penalty: 1,
  seed: '',
};

export async function createSession(
  orchestratorUrl,
  { model, speculativeDraftModelUrl, speculativeDraftK, sampling },
) {
  const body = { model };
  if (speculativeDraftModelUrl) {
    body.speculative_draft_model_url = speculativeDraftModelUrl;
    body.speculative_draft_k = Number(speculativeDraftK) || 4;
  }
  if (sampling) {
    const num = (v, fallback) => (v === '' || v === null || v === undefined ? fallback : Number(v));
    body.temp = num(sampling.temp, 0);
    body.top_k = num(sampling.top_k, 1);
    body.top_p = num(sampling.top_p, 1);
    body.min_p = num(sampling.min_p, 0);
    body.repeat_penalty = num(sampling.repeat_penalty, 1);
    // Empty seed means "random each run" -- the runtime's LLAMA_DEFAULT_SEED.
    if (sampling.seed !== '' && sampling.seed !== null && sampling.seed !== undefined) {
      body.seed = Number(sampling.seed);
    }
  }
  return postJson(`${orchestratorUrl}/session/create`, body);
}

export async function generate(orchestratorUrl, { sessionId, prompt, maxTokens }) {
  return postJson(`${orchestratorUrl}/session/generate`, {
    session_id: sessionId,
    prompt,
    max_tokens: maxTokens || 64,
    chat: true,
  });
}

export async function destroySession(orchestratorUrl, sessionId) {
  return postJson(`${orchestratorUrl}/session/destroy`, { session_id: sessionId });
}

// --- node_agent (local or remote -- same API, CORS-enabled) -----------

export async function fetchNodeStatus(node) {
  return getJson(`${nodeAgentBase(node)}/status`);
}

export async function fetchNodeLog(node, { worker, lines } = {}) {
  const params = new URLSearchParams();
  if (worker) params.set('worker', worker);
  params.set('lines', String(lines || 300));
  const res = await fetch(`${nodeAgentBase(node)}/debug/log?${params.toString()}`);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`HTTP ${res.status}`);
    err.status = res.status;
    err.body = text;
    throw err;
  }
  return res.text();
}
