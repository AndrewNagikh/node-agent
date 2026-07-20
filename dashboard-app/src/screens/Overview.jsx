import React from 'react';
import { COLORS, mono, roleColor, fmtAgo } from '../theme.js';

function Card({ node, isLocal, onOpen, onUpdate, updateJob }) {
  const online = node.agentOnline;
  const dotCol = online ? COLORS.green : COLORS.red;
  const ws = node.status?.worker_states || {};
  const workers = Object.entries(ws).filter(([, v]) => v && v !== 'STOPPED');

  let btnLabel = 'Обновить и перезапустить';
  let btnCol = COLORS.greenSoft;
  let btnBorder = 'rgba(86,227,154,.45)';
  let btnBg = 'rgba(86,227,154,.08)';
  let btnDisabled = false;
  if (updateJob && updateJob.state !== 'done' && updateJob.state !== 'failed') {
    btnLabel = { pulling: 'git pull…', building: 'сборка…', restarting: 'перезапуск…' }[updateJob.state] || 'обновляю…';
    btnCol = COLORS.amber;
    btnBorder = 'rgba(255,200,98,.45)';
    btnBg = 'rgba(255,200,98,.07)';
    btnDisabled = true;
  } else if (!online) {
    btnDisabled = true;
  }

  return (
    <div
      onClick={onOpen}
      style={{
        background: COLORS.cardBg, border: `1px solid ${online ? COLORS.border : 'rgba(255,107,107,.35)'}`,
        borderRadius: 9, padding: '14px 16px', cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 10,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ width: 9, height: 9, borderRadius: '50%', flex: 'none', background: dotCol }} />
        <span style={{ ...mono, fontWeight: 700, fontSize: 14.5, color: COLORS.textBright }}>{node.node_id}</span>
        <span style={{ ...mono, fontSize: 11, color: COLORS.dim2 }}>{node.host}:{node.port}</span>
        {isLocal && (
          <span style={{ ...mono, fontSize: 10, padding: '2px 7px', borderRadius: 4, border: '1px solid rgba(86,227,154,.4)', color: COLORS.green }}>
            эта машина
          </span>
        )}
        <span
          style={{
            marginLeft: isLocal ? 0 : 'auto', ...mono, fontSize: 10.5, padding: '2px 8px', borderRadius: 4,
            border: `1px solid ${roleColor(node.role)}55`, color: roleColor(node.role),
          }}
        >
          {node.role || 'idle'}
        </span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8, ...mono, fontSize: 11.5 }}>
        <span style={{ color: online ? COLORS.green : COLORS.red }}>● {online ? 'онлайн' : `офлайн · ${fmtAgo(node.last_seen)}`}</span>
        <span style={{ color: '#3a4756' }}>│</span>
        <span style={{ color: COLORS.dim }}>{node.system?.os || '—'}</span>
      </div>

      <div style={{ display: 'flex', gap: 14, ...mono, fontSize: 11.5, color: COLORS.dim }}>
        <span>GPU <span style={{ color: COLORS.faint }}>{node.hardware?.gpu_name || '—'}</span></span>
        <span>VRAM <span style={{ color: COLORS.faint }}>{node.hardware?.gpu_vram_gb ?? '—'} GB</span></span>
        <span>score <span style={{ color: COLORS.green }}>{node.score != null ? node.score.toFixed(0) : '—'}</span></span>
      </div>

      {workers.length > 0 && (
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap' }}>
          {workers.map(([role, state]) => {
            const c = state === 'READY' ? COLORS.green : state === 'FAILED' ? COLORS.red : COLORS.amber;
            return (
              <span key={role} style={{ ...mono, fontSize: 10.5, letterSpacing: '.05em', padding: '3px 9px', borderRadius: 4, border: `1px solid ${c}55`, color: c, background: `${c}12` }}>
                {role.toUpperCase()} · {state}
              </span>
            );
          })}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 2 }}>
        {isLocal ? (
          <button
            onClick={(e) => { e.stopPropagation(); if (!btnDisabled) onUpdate(); }}
            disabled={btnDisabled}
            style={{
              display: 'flex', alignItems: 'center', gap: 7, padding: '6px 12px', borderRadius: 6, fontSize: 12,
              fontWeight: 500, cursor: btnDisabled ? 'default' : 'pointer', border: `1px solid ${btnBorder}`, background: btnBg, color: btnCol,
            }}
          >
            {btnLabel}
          </button>
        ) : (
          <span style={{ ...mono, fontSize: 11, color: COLORS.dim3 }}>управление — только с этой машины</span>
        )}
        <span style={{ ...mono, fontSize: 11, color: COLORS.dim2 }}>детали →</span>
      </div>
    </div>
  );
}

export default function Overview({ nodes, localNodeId, openNode, onUpdate, updateJobs, orchestratorOnline, orchestratorUrl }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(390px,1fr))', gap: 14 }}>
      <div
        style={{
          background: COLORS.cardBg, border: `1px solid ${orchestratorOnline ? COLORS.border : 'rgba(255,107,107,.35)'}`,
          borderRadius: 9, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 10,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div style={{ width: 9, height: 9, borderRadius: '50%', flex: 'none', background: orchestratorOnline ? COLORS.green : COLORS.red }} />
          <span style={{ ...mono, fontWeight: 700, fontSize: 14.5, color: COLORS.textBright }}>orchestrator</span>
          <span style={{ ...mono, fontSize: 11, color: COLORS.dim2 }}>{orchestratorUrl}</span>
          <span style={{ marginLeft: 'auto', ...mono, fontSize: 10.5, padding: '2px 8px', borderRadius: 4, border: `1px solid ${COLORS.green}55`, color: COLORS.green }}>
            ОРКЕСТРАТОР
          </span>
        </div>
        <div style={{ ...mono, fontSize: 11.5, color: orchestratorOnline ? COLORS.green : COLORS.red }}>
          ● {orchestratorOnline ? 'онлайн' : 'нет связи'}
        </div>
        <div style={{ ...mono, fontSize: 11, color: COLORS.dim3 }}>обновление — вручную по SSH (запускается отдельно на Linux-сервере)</div>
      </div>

      {nodes.map((n) => (
        <Card
          key={n.node_id}
          node={n}
          isLocal={n.node_id === localNodeId}
          onOpen={() => openNode(n.node_id)}
          onUpdate={() => onUpdate(n.node_id)}
          updateJob={updateJobs[n.node_id]}
        />
      ))}
    </div>
  );
}
