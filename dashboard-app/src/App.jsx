import React, { useEffect, useRef, useState } from 'react';
import { COLORS, mono } from './theme.js';
import { fetchNodes, fetchNodeStatus, fetchModels, createSession, generate, destroySession } from './api.js';
import Sidebar from './components/Sidebar.jsx';
import ConfirmModal from './components/ConfirmModal.jsx';
import Overview from './screens/Overview.jsx';
import NodeDetail from './screens/NodeDetail.jsx';
import Sessions from './screens/Sessions.jsx';
import Models from './screens/Models.jsx';

function Setup({ onSaved }) {
  const [nodeId, setNodeId] = useState('node-a');
  const [orchestrator, setOrchestrator] = useState('');
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    const cfg = await window.dashboard.saveConfig({ nodeId, orchestrator: orchestrator || undefined });
    await window.dashboard.startAgent();
    setSaving(false);
    onSaved(cfg);
  };

  return (
    <div style={{ maxWidth: 420, margin: '80px auto', color: COLORS.text }}>
      <div style={{ fontSize: 18, fontWeight: 600, marginBottom: 4 }}>Первый запуск на этой машине</div>
      <div style={{ fontSize: 12.5, color: COLORS.dim, marginBottom: 20 }}>
        Запуск приложения = регистрация этой машины как ноды в оркестраторе.
      </div>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12, color: COLORS.dim, marginBottom: 14 }}>
        node id
        <select value={nodeId} onChange={(e) => setNodeId(e.target.value)} style={{ background: COLORS.bg, border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: '8px 10px', color: COLORS.text, ...mono }}>
          <option value="node-a">node-a</option>
          <option value="node-b">node-b</option>
          <option value="node-c">node-c</option>
        </select>
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12, color: COLORS.dim, marginBottom: 20 }}>
        orchestrator url <span style={{ color: COLORS.dim3 }}>(пусто — взять из nodes.conf)</span>
        <input value={orchestrator} onChange={(e) => setOrchestrator(e.target.value)} placeholder="http://192.168.50.154:9000" style={{ background: COLORS.bg, border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: '8px 10px', color: COLORS.text, ...mono }} />
      </label>
      <button onClick={save} disabled={saving} style={{ padding: '8px 16px', borderRadius: 6, fontWeight: 500, cursor: saving ? 'default' : 'pointer', border: '1px solid rgba(86,227,154,.5)', background: 'rgba(86,227,154,.12)', color: '#7bebac' }}>
        {saving ? 'Запускаю…' : 'Сохранить и запустить'}
      </button>
    </div>
  );
}

export default function App() {
  const [cfg, setCfg] = useState(null);
  const [loading, setLoading] = useState(true);

  const [nodes, setNodes] = useState([]);
  const [orchestratorOnline, setOrchestratorOnline] = useState(false);
  const [models, setModels] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [genStates, setGenStates] = useState({});

  const [view, setView] = useState('overview');
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [confirm, setConfirm] = useState(null);

  const [localAgentRunning, setLocalAgentRunning] = useState(false);
  const [localLog, setLocalLog] = useState([]);
  const [updateJobs, setUpdateJobs] = useState({});

  const lastSessionRef = useRef(null);

  // --- initial config + IPC subscriptions -------------------------------
  useEffect(() => {
    (async () => {
      const c = await window.dashboard.getConfig();
      setCfg(c && c.nodeId ? c : null);
      setLoading(false);
      if (c && c.nodeId) {
        const state = await window.dashboard.getAgentState();
        setLocalAgentRunning(state.running);
        setLocalLog(await window.dashboard.getAgentLog());
      }
    })();

    const offLog = window.dashboard.onAgentLogLine((line) => setLocalLog((prev) => [...prev.slice(-1999), line]));
    const offState = window.dashboard.onAgentState((s) => setLocalAgentRunning(!!s.running));
    const offUpdate = window.dashboard.onUpdateProgress((p) => {
      setUpdateJobs((prev) => {
        const nodeId = cfg?.nodeId;
        if (!nodeId) return prev;
        const existing = prev[nodeId] || { log: [] };
        const next = { ...existing, state: p.state };
        if (p.line) next.log = [...existing.log, p.line];
        if (p.state === 'failed') next.error = p.line || 'update failed';
        return { ...prev, [nodeId]: next };
      });
    });
    return () => {
      offLog();
      offState();
      offUpdate();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- cluster polling ----------------------------------------------------
  useEffect(() => {
    if (!cfg?.orchestrator) return undefined;
    let cancelled = false;

    async function poll() {
      let baseNodes = [];
      try {
        baseNodes = await fetchNodes(cfg.orchestrator);
        if (!cancelled) setOrchestratorOnline(true);
      } catch {
        if (!cancelled) setOrchestratorOnline(false);
      }

      const enriched = await Promise.all(
        baseNodes.map(async (n) => {
          const node = { ...n, port: n.port };
          try {
            const status = await fetchNodeStatus(node);
            return { ...node, agentOnline: true, status };
          } catch {
            return { ...node, agentOnline: false, status: null };
          }
        }),
      );

      const pipeline = lastSessionRef.current?.pipeline || [];
      const withRole = enriched.map((n) => ({
        ...n,
        role: pipeline.find((p) => p.node_id === n.node_id)?.role,
      }));

      if (!cancelled) setNodes(withRole);

      try {
        const m = await fetchModels(cfg.orchestrator);
        if (!cancelled) setModels(Array.isArray(m) ? m : []);
      } catch {
        /* orchestrator unreachable this cycle; keep last known models */
      }
    }

    poll();
    const id = setInterval(poll, 2500);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [cfg?.orchestrator, sessions.length]);

  if (loading) return null;
  if (!cfg) return <Setup onSaved={setCfg} />;

  // --- actions --------------------------------------------------------------

  const openNode = (nodeId) => {
    setSelectedNodeId(nodeId);
    setView('node');
  };

  const requestUpdate = (nodeId) => {
    if (nodeId !== cfg.nodeId) return; // never remote
    const usedBy = sessions.find((s) => (s.pipeline || []).some((p) => p.node_id === nodeId));
    setConfirm({
      title: `Обновить и перезапустить ${nodeId}?`,
      body: `git pull + пересборка + рестарт node_agent на ${nodeId}.`,
      warn: usedBy ? `Сессия ${usedBy.session_id} использует эту ноду — она прервётся.` : null,
      okLabel: 'Обновить и перезапустить',
      onConfirm: async () => {
        setConfirm(null);
        setUpdateJobs((prev) => ({ ...prev, [nodeId]: { state: 'pulling', log: [] } }));
        await window.dashboard.runUpdate();
      },
    });
  };

  const requestDestroy = (sessionId) => {
    setConfirm({
      title: `Удалить сессию ${sessionId}?`,
      body: 'POST /session/destroy — воркеры на всех нодах будут остановлены, KV-cache сброшен.',
      okLabel: 'Удалить',
      onConfirm: async () => {
        setConfirm(null);
        try {
          await destroySession(cfg.orchestrator, sessionId);
        } catch {
          /* already gone server-side is fine */
        }
        setSessions((prev) => prev.filter((s) => s.session_id !== sessionId));
        if (lastSessionRef.current?.session_id === sessionId) lastSessionRef.current = null;
      },
    });
  };

  const handleCreateSession = async ({ model, speculativeDraftModelUrl, speculativeDraftK }) => {
    const res = await createSession(cfg.orchestrator, { model, speculativeDraftModelUrl, speculativeDraftK });
    const session = {
      session_id: res.session_id,
      model,
      pipeline: res.pipeline || [],
      speculative: !!speculativeDraftModelUrl,
      draftK: speculativeDraftK,
    };
    lastSessionRef.current = session;
    setSessions((prev) => [...prev, session]);
  };

  const handleGenerate = async (sessionId) => {
    const gs = genStates[sessionId] || { prompt: '' };
    setGenStates((prev) => ({ ...prev, [sessionId]: { ...gs, running: true, error: null } }));
    try {
      const maxTokens = Number(gs.maxTokens) || 64;
      const res = await generate(cfg.orchestrator, { sessionId, prompt: gs.prompt, maxTokens });
      setGenStates((prev) => ({ ...prev, [sessionId]: { ...prev[sessionId], running: false, result: res } }));
    } catch (e) {
      setGenStates((prev) => ({ ...prev, [sessionId]: { ...prev[sessionId], running: false, error: e.message } }));
    }
  };

  const setPrompt = (sessionId, value) => {
    setGenStates((prev) => ({ ...prev, [sessionId]: { ...(prev[sessionId] || {}), prompt: value } }));
  };

  const setMaxTokens = (sessionId, value) => {
    setGenStates((prev) => ({ ...prev, [sessionId]: { ...(prev[sessionId] || {}), maxTokens: value } }));
  };

  // Plain computed value, not useMemo: this function already sits after
  // the loading/!cfg early returns below, so a hook here would change the
  // number of hooks called between the "still loading" and "ready" renders
  // -- exactly the Rules-of-Hooks violation that blanked the screen.
  const genStatesForScreen = {};
  for (const s of sessions) {
    genStatesForScreen[s.session_id] = { ...(genStates[s.session_id] || { prompt: '', maxTokens: 64 }), setPrompt, setMaxTokens };
  }

  const selectedNode = nodes.find((n) => n.node_id === selectedNodeId);
  const titles = {
    overview: ['Обзор кластера', 'GET /nodes + GET /status на каждом node_agent'],
    sessions: ['Сессии', 'POST /session/create · /session/generate · /session/destroy'],
    models: ['Модели', 'GET /models'],
    node: selectedNode ? [selectedNode.node_id, `${selectedNode.host}:${selectedNode.port}`] : ['', ''],
  };
  const [titleMain, titleSub] = titles[view];

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', fontFamily: "'IBM Plex Sans',sans-serif", fontSize: 13, background: COLORS.bg }}>
      <Sidebar
        view={view}
        setView={(v) => { setView(v); setSelectedNodeId(null); }}
        nodes={nodes}
        selectedNodeId={selectedNodeId}
        openNode={openNode}
        localNodeId={cfg.nodeId}
        sessions={sessions}
        models={models}
      />

      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '13px 22px', borderBottom: `1px solid ${COLORS.borderDim}`, background: COLORS.headerBg }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 600, color: COLORS.textHeader }}>{titleMain}</div>
            <div style={{ ...mono, fontSize: 11, color: COLORS.dim2, marginTop: 2 }}>{titleSub}</div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 11px', border: `1px solid ${COLORS.border}`, borderRadius: 6, background: '#101720' }}>
              <div style={{ width: 7, height: 7, borderRadius: '50%', background: nodes.every((n) => n.agentOnline) && nodes.length ? COLORS.green : COLORS.red }} />
              <span style={{ ...mono, fontSize: 11.5, color: '#aeb9c4', whiteSpace: 'nowrap' }}>онлайн {nodes.filter((n) => n.agentOnline).length}/{nodes.length}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 11px', border: `1px solid ${COLORS.border}`, borderRadius: 6, background: '#101720' }}>
              <span style={{ ...mono, fontSize: 11.5, color: '#aeb9c4' }}>сессий: {sessions.length}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7, padding: '5px 11px', border: `1px solid ${COLORS.border}`, borderRadius: 6, background: '#101720' }}>
              <span style={{ ...mono, fontSize: 11.5, color: COLORS.dim2 }}>poll 2.5s</span>
            </div>
          </div>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: '18px 22px' }}>
          {view === 'overview' && (
            <Overview
              nodes={nodes}
              localNodeId={cfg.nodeId}
              openNode={openNode}
              onUpdate={requestUpdate}
              updateJobs={updateJobs}
              orchestratorOnline={orchestratorOnline}
              orchestratorUrl={cfg.orchestrator}
            />
          )}
          {view === 'node' && selectedNode && (
            <NodeDetail
              node={selectedNode}
              isLocal={selectedNode.node_id === cfg.nodeId}
              localLog={localLog}
              onUpdate={() => requestUpdate(selectedNode.node_id)}
              updateJob={updateJobs[selectedNode.node_id]}
              onBack={() => setView('overview')}
            />
          )}
          {view === 'sessions' && (
            <Sessions
              sessions={sessions}
              models={models}
              onCreate={handleCreateSession}
              onDestroy={requestDestroy}
              onGenerate={handleGenerate}
              genStates={genStatesForScreen}
            />
          )}
          {view === 'models' && <Models models={models} />}
        </div>
      </div>

      <ConfirmModal confirm={confirm} onCancel={() => setConfirm(null)} onConfirm={() => confirm && confirm.onConfirm()} />
    </div>
  );
}
