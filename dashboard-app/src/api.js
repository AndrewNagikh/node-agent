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

// Human-readable labels for the phases the orchestrator reports while a
// session is being created. Keys must match progress_set() in orchestrator.cpp.
export const SESSION_PHASES = [
  ['queued', 'Постановка в очередь'],
  ['coverage_check', 'Проверка слоёв на нодах'],
  ['services', 'Настройка сервисов'],
  ['prepare_nodes', 'Загрузка весов на ноды'],
  ['draft_fetch', 'Загрузка драфт-модели'],
  ['spawn_workers', 'Запуск воркеров'],
  ['await_workers', 'Ожидание готовности'],
  ['ready', 'Готово'],
];

// --- model install / repair -------------------------------------------
//
// Repair is the same three-step pipeline used by hand: compute what's
// missing, run it, then re-check coverage. Kept as separate calls so the UI
// can show the download size before starting and track the job while it runs.

export async function fetchInstallPlan(orchestratorUrl, modelId) {
  const res = await postJson(`${orchestratorUrl}/models/${modelId}/install-plan`, {});
  const plan = res.install_plan || {};
  const ops = plan.operations || [];
  return {
    operationCount: plan.operation_count ?? ops.length,
    downloadBytes: plan.total_download_bytes ?? 0,
    downloads: ops.filter((o) => o.action === 'DOWNLOAD').length,
    deletes: ops.filter((o) => o.action === 'DELETE').length,
  };
}

export async function startInstall(orchestratorUrl, modelId) {
  return postJson(`${orchestratorUrl}/models/${modelId}/install/execute`, {});
}

export async function fetchJob(orchestratorUrl, jobId) {
  const res = await fetch(`${orchestratorUrl}/jobs/${jobId}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const job = await res.json();
  // The job reports per-node counters; the UI wants one number.
  let total = 0;
  let ready = 0;
  let failed = 0;
  for (const n of Object.values(job.nodes || {})) {
    total += n.total_count || 0;
    ready += n.ready_count || 0;
    failed += n.failed_count || 0;
  }
  return { state: job.state, total, ready, failed, error: job.error || '' };
}

export async function refreshCoverage(orchestratorUrl, modelId) {
  return postJson(`${orchestratorUrl}/models/${modelId}/coverage/refresh`, {});
}

export async function searchHuggingFace(orchestratorUrl, query, limit = 24) {
  const url = `${orchestratorUrl}/hf/search?limit=${limit}${query ? `&q=${encodeURIComponent(query)}` : ''}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  return res.json();
}

// Heuristic shortlist of possible speculative draft models. HF has no
// declared draft-pair field, so this is candidates to measure, never a
// promise -- acceptance is strongly pair-dependent (x1.64 on one pair, 19%
// and no speedup on another).
export async function fetchDraftCandidates(orchestratorUrl, repository) {
  const res = await fetch(`${orchestratorUrl}/hf/draft-candidates?repo=${encodeURIComponent(repository)}`);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  return res.json();
}

export async function fetchHuggingFaceFiles(orchestratorUrl, repository) {
  const res = await fetch(`${orchestratorUrl}/hf/files?repo=${encodeURIComponent(repository)}`);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).error || `HTTP ${res.status}`);
  return res.json();
}

// Registers a browsed model so the normal install pipeline can take over.
// modelId is derived from the filename, which is what the rest of the system
// keys everything on.
export async function registerModel(orchestratorUrl, { modelId, repository, filename }) {
  return postJson(`${orchestratorUrl}/models/register`, {
    model_id: modelId,
    source: 'huggingface',
    repository,
    filename,
    revision: 'main',
  });
}

export async function fetchSessionProgress(orchestratorUrl, progressId) {
  const res = await fetch(`${orchestratorUrl}/session/progress/${encodeURIComponent(progressId)}`);
  if (!res.ok) return null;
  return res.json();
}

export async function createSession(
  orchestratorUrl,
  { model, speculativeDraftModelUrl, speculativeDraftK, sampling, progressId },
) {
  const body = { model };
  if (progressId) body.progress_id = progressId;
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

// `messages` is the full conversation so far ({role, content}); the runtime
// renders it through the model's chat template. `prompt` remains accepted by
// the API for non-chat callers but the dashboard always sends a conversation.
export async function generate(orchestratorUrl, { sessionId, messages, maxTokens }) {
  return postJson(`${orchestratorUrl}/session/generate`, {
    session_id: sessionId,
    messages,
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
