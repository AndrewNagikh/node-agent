import React from 'react';
import { COLORS, mono, roleColor } from '../theme.js';

const NAV = [
  { view: 'overview', label: 'Обзор кластера' },
  { view: 'sessions', label: 'Сессии' },
  { view: 'models', label: 'Модели' },
];

export default function Sidebar({ view, setView, nodes, selectedNodeId, openNode, localNodeId, sessions, models }) {
  const badges = {
    overview: `${nodes.filter((n) => n.agentOnline).length}/${nodes.length}`,
    sessions: String(sessions.length),
    models: String(models.length),
  };

  return (
    <div style={{ width: 226, flex: 'none', display: 'flex', flexDirection: 'column', background: COLORS.panelBg, borderRight: `1px solid ${COLORS.borderDim}` }}>
      <div style={{ padding: '16px 16px 12px', borderBottom: `1px solid ${COLORS.borderDim}` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: COLORS.green }} />
          <div style={{ ...mono, fontWeight: 700, fontSize: 14, color: COLORS.textBright }}>distributed-llm</div>
        </div>
        <div style={{ ...mono, fontSize: 10.5, color: COLORS.dim2, marginTop: 4 }}>
          {localNodeId ? `эта машина: ${localNodeId}` : 'нода не настроена'}
        </div>
      </div>

      <div style={{ padding: '12px 10px 4px' }}>
        <div style={{ ...mono, fontSize: 10, letterSpacing: '.14em', color: COLORS.dim2, padding: '0 8px 6px' }}>── УПРАВЛЕНИЕ ──</div>
        {NAV.map((n) => {
          const active = view === n.view;
          return (
            <div
              key={n.view}
              onClick={() => setView(n.view)}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 10px',
                borderRadius: 6, cursor: 'pointer', marginBottom: 2,
                background: active ? COLORS.navActive : 'transparent',
                color: active ? COLORS.greenText : '#aeb9c4',
              }}
            >
              <span>{n.label}</span>
              <span style={{ ...mono, fontSize: 10.5, color: COLORS.dim2 }}>{badges[n.view]}</span>
            </div>
          );
        })}
      </div>

      <div style={{ padding: '10px 10px 4px', flex: 1, overflowY: 'auto' }}>
        <div style={{ ...mono, fontSize: 10, letterSpacing: '.14em', color: COLORS.dim2, padding: '0 8px 6px' }}>── УЗЛЫ ──</div>
        {nodes.map((n) => {
          const active = view === 'node' && selectedNodeId === n.node_id;
          const dotCol = n.agentOnline ? COLORS.green : COLORS.red;
          return (
            <div
              key={n.node_id}
              onClick={() => openNode(n.node_id)}
              style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', borderRadius: 6,
                cursor: 'pointer', marginBottom: 2, background: active ? COLORS.navHover : 'transparent',
              }}
            >
              <div style={{ width: 7, height: 7, borderRadius: '50%', flex: 'none', background: dotCol }} />
              <span style={{ ...mono, fontSize: 12, color: '#c6d2dc', flex: 1 }}>{n.node_id}</span>
              <span style={{ ...mono, fontSize: 10, color: roleColor(n.role) }}>{n.role || ''}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
