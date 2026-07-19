import React, { useEffect, useRef, useState } from 'react';

const COLORS = {
  bg: '#0a0e13',
  panel: '#121820',
  border: '#1f2934',
  text: '#d7e0e8',
  dim: '#7d8b99',
  green: '#56e39a',
  amber: '#ffc862',
  red: '#ff6b6b',
};

const mono = { fontFamily: "'JetBrains Mono', monospace" };

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
        <select
          value={nodeId}
          onChange={(e) => setNodeId(e.target.value)}
          style={{ background: COLORS.bg, border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: '8px 10px', color: COLORS.text, ...mono }}
        >
          <option value="node-a">node-a</option>
          <option value="node-b">node-b</option>
          <option value="node-c">node-c</option>
        </select>
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 12, color: COLORS.dim, marginBottom: 20 }}>
        orchestrator url <span style={{ color: '#4a5866' }}>(пусто — взять из nodes.conf)</span>
        <input
          value={orchestrator}
          onChange={(e) => setOrchestrator(e.target.value)}
          placeholder="http://192.168.50.154:9000"
          style={{ background: COLORS.bg, border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: '8px 10px', color: COLORS.text, ...mono }}
        />
      </label>
      <button
        onClick={save}
        disabled={saving}
        style={{
          padding: '8px 16px', borderRadius: 6, fontWeight: 500, cursor: saving ? 'default' : 'pointer',
          border: `1px solid rgba(86,227,154,.5)`, background: 'rgba(86,227,154,.12)', color: '#7bebac',
        }}
      >
        {saving ? 'Запускаю…' : 'Сохранить и запустить'}
      </button>
    </div>
  );
}

export default function App() {
  const [cfg, setCfg] = useState(null);
  const [loading, setLoading] = useState(true);
  const [agentRunning, setAgentRunning] = useState(false);
  const [logLines, setLogLines] = useState([]);
  const [updateState, setUpdateState] = useState(null); // null | 'pulling' | 'building' | 'restarting' | 'done' | 'failed'
  const logRef = useRef(null);

  useEffect(() => {
    (async () => {
      const c = await window.dashboard.getConfig();
      setCfg(c && c.nodeId ? c : null);
      setLoading(false);
      if (c && c.nodeId) {
        const state = await window.dashboard.getAgentState();
        setAgentRunning(state.running);
        setLogLines(await window.dashboard.getAgentLog());
      }
    })();

    const offLog = window.dashboard.onAgentLogLine((line) => {
      setLogLines((prev) => [...prev.slice(-1999), line]);
    });
    const offState = window.dashboard.onAgentState((s) => setAgentRunning(!!s.running));
    const offUpdate = window.dashboard.onUpdateProgress((p) => setUpdateState(p.state));
    return () => {
      offLog();
      offState();
      offUpdate();
    };
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logLines]);

  if (loading) return null;
  if (!cfg) return <Setup onSaved={setCfg} />;

  const runUpdate = async () => {
    setUpdateState('pulling');
    const res = await window.dashboard.runUpdate();
    if (!res.ok) setUpdateState('failed');
  };

  const stateColor = { pulling: COLORS.amber, building: COLORS.amber, restarting: COLORS.amber, done: COLORS.green, failed: COLORS.red }[updateState];
  const updateBusy = updateState && updateState !== 'done' && updateState !== 'failed';

  return (
    <div style={{ minHeight: '100vh', background: COLORS.bg, color: COLORS.text, padding: 24, boxSizing: 'border-box' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
        <div
          style={{
            width: 10, height: 10, borderRadius: '50%',
            background: agentRunning ? COLORS.green : COLORS.red,
          }}
        />
        <div style={{ fontWeight: 700, fontSize: 16, ...mono }}>{cfg.nodeId}</div>
        <span style={{ fontSize: 12, color: COLORS.dim, ...mono }}>{agentRunning ? 'node_agent запущен' : 'node_agent остановлен'}</span>
      </div>
      <div style={{ fontSize: 11.5, color: COLORS.dim, marginBottom: 20, ...mono }}>
        orchestrator: {cfg.orchestrator || '(не задан)'}
      </div>

      <button
        onClick={runUpdate}
        disabled={updateBusy}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 16px', borderRadius: 6,
          fontSize: 12.5, fontWeight: 500, cursor: updateBusy ? 'default' : 'pointer', marginBottom: 6,
          border: `1px solid ${updateBusy ? 'rgba(255,200,98,.45)' : 'rgba(86,227,154,.45)'}`,
          background: updateBusy ? 'rgba(255,200,98,.08)' : 'rgba(86,227,154,.08)',
          color: updateBusy ? '#ffc862' : '#8fd9b0',
        }}
      >
        {updateBusy ? `${updateState}…` : 'Обновить и перезапустить'}
      </button>
      {updateState && (
        <div style={{ fontSize: 11.5, color: stateColor, marginBottom: 14, ...mono }}>статус обновления: {updateState}</div>
      )}

      <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 8 }}>Логи node_agent</div>
      <div
        ref={logRef}
        style={{
          background: '#0a0e13', border: `1px solid ${COLORS.border}`, borderRadius: 7, padding: '11px 14px',
          height: 480, overflowY: 'auto', fontSize: 11.5, lineHeight: 1.6, ...mono,
        }}
      >
        {logLines.map((l, i) => (
          <div key={i} style={{ color: '#a9b8c6', whiteSpace: 'pre-wrap' }}>{l}</div>
        ))}
      </div>
    </div>
  );
}
