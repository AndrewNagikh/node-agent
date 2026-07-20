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

export async function createSession(orchestratorUrl, { model, speculativeDraftModelUrl, speculativeDraftK }) {
  const body = { model };
  if (speculativeDraftModelUrl) {
    body.speculative_draft_model_url = speculativeDraftModelUrl;
    body.speculative_draft_k = Number(speculativeDraftK) || 4;
  }
  return postJson(`${orchestratorUrl}/session/create`, body);
}

export async function generate(orchestratorUrl, { sessionId, prompt, maxTokens }) {
  return postJson(`${orchestratorUrl}/session/generate`, {
    session_id: sessionId,
    prompt,
    max_tokens: maxTokens || 64,
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
