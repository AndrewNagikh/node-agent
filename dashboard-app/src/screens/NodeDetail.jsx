import React, { useEffect, useRef, useState } from 'react';
import { COLORS, mono, roleColor, fmtAgo } from '../theme.js';
import { fetchNodeLog } from '../api.js';

const STEP_NAMES = [
  { key: 'pulling', label: 'git pull' },
  { key: 'building', label: 'build' },
  { key: 'restarting', label: 'restart' },
];
const STEP_ORDER = { pulling: 0, building: 1, restarting: 2, done: 3 };

function JobSteps({ job }) {
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      {STEP_NAMES.map((s, i) => {
        let col = COLORS.dim3;
        let border = '#253141';
        let bg = 'transparent';
        if (job) {
          if (job.state === 'failed') {
            if (i < STEP_ORDER[job.failedAt ?? 'building']) { col = COLORS.green; border = 'rgba(86,227,154,.4)'; }
            else if (i === STEP_ORDER[job.failedAt ?? 'building']) { col = COLORS.red; border = 'rgba(255,107,107,.5)'; bg = 'rgba(255,107,107,.07)'; }
          } else {
            const cur = STEP_ORDER[job.state];
            if (i < cur || job.state === 'done') { col = COLORS.green; border = 'rgba(86,227,154,.4)'; }
            else if (i === cur) { col = COLORS.amber; border = 'rgba(255,200,98,.5)'; bg = 'rgba(255,200,98,.07)'; }
          }
        }
        return (
          <span key={s.key} style={{ ...mono, fontSize: 11, padding: '4px 11px', borderRadius: 20, border: `1px solid ${border}`, color: col, background: bg }}>
            {i + 1} {s.label}
          </span>
        );
      })}
    </div>
  );
}

export default function NodeDetail({ node, isLocal, localLog, onUpdate, updateJob, onBack }) {
  const [remoteLog, setRemoteLog] = useState('');
  const [logFilter, setLogFilter] = useState('все');
  const [remoteError, setRemoteError] = useState(null);
  const logRef = useRef(null);

  useEffect(() => {
    if (isLocal) return undefined;
    let cancelled = false;
    async function poll() {
      try {
        const worker = logFilter !== 'все' && logFilter !== 'node_agent' ? logFilter : undefined;
        const text = await fetchNodeLog(node, { worker, lines: 400 });
        if (!cancelled) {
          setRemoteLog(text);
          setRemoteError(null);
        }
      } catch (e) {
        if (!cancelled) setRemoteError(e.message);
      }
    }
    poll();
    const id = setInterval(poll, 2500);
    return () => { cancelled = true; clearInterval(id); };
  }, [isLocal, node.host, node.port, logFilter]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  });

  const online = node.agentOnline;
  const ws = node.status?.worker_states || {};
  const workers = Object.entries(ws).filter(([, v]) => v && v !== 'STOPPED');

  const localLines = isLocal
    ? (logFilter === 'все' ? localLog : localLog.filter((l) => l.includes(`[${logFilter}]`) || logFilter === 'node_agent'))
    : null;

  const busy = updateJob && updateJob.state !== 'done' && updateJob.state !== 'failed';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 1100 }}>
      <div>
        <span onClick={onBack} style={{ ...mono, fontSize: 11.5, color: COLORS.green, cursor: 'pointer' }}>← обзор кластера</span>
      </div>

      <div style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.border}`, borderRadius: 9, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 11 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 10, height: 10, borderRadius: '50%', background: online ? COLORS.green : COLORS.red }} />
          <span style={{ ...mono, fontWeight: 700, fontSize: 17, color: COLORS.textBright }}>{node.node_id}</span>
          <span style={{ ...mono, fontSize: 12, color: COLORS.dim2 }}>{node.host}:{node.port}</span>
          <span style={{ ...mono, fontSize: 12, color: online ? COLORS.green : COLORS.red, whiteSpace: 'nowrap' }}>
            ● {online ? 'онлайн' : `офлайн · ${fmtAgo(node.last_seen)}`}
          </span>
          <span style={{ marginLeft: 'auto', ...mono, fontSize: 11, padding: '3px 10px', borderRadius: 4, border: `1px solid ${roleColor(node.role)}55`, color: roleColor(node.role) }}>
            {(node.role || 'idle').toUpperCase()}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 18, ...mono, fontSize: 12, color: COLORS.dim, flexWrap: 'wrap' }}>
          <span>{node.system?.os || '—'} · {node.hardware?.cpu_threads ?? '—'} threads</span>
          <span>GPU <span style={{ color: COLORS.faint }}>{node.hardware?.gpu_name || '—'}</span></span>
          <span>VRAM <span style={{ color: COLORS.faint }}>{node.hardware?.gpu_vram_gb ?? '—'} GB</span></span>
          <span>score <span style={{ color: COLORS.green }}>{node.score != null ? node.score.toFixed(1) : '—'}</span></span>
        </div>
        {workers.length > 0 && (
          <div style={{ display: 'flex', gap: 8 }}>
            {workers.map(([role, state]) => {
              const c = state === 'READY' ? COLORS.green : state === 'FAILED' ? COLORS.red : COLORS.amber;
              return (
                <span key={role} style={{ ...mono, fontSize: 11, letterSpacing: '.05em', padding: '4px 11px', borderRadius: 4, border: `1px solid ${c}55`, color: c, background: `${c}12` }}>
                  {role.toUpperCase()} · {state}
                </span>
              );
            })}
          </div>
        )}
      </div>

      {isLocal ? (
        <div style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.border}`, borderRadius: 9, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            <div style={{ fontWeight: 600, fontSize: 13.5, color: COLORS.textHeader }}>Обновление</div>
            <button
              onClick={() => !busy && onUpdate()}
              disabled={busy}
              style={{
                padding: '7px 14px', borderRadius: 6, fontSize: 12.5, fontWeight: 500, cursor: busy ? 'default' : 'pointer',
                border: `1px solid ${busy ? 'rgba(255,200,98,.45)' : 'rgba(86,227,154,.45)'}`,
                background: busy ? 'rgba(255,200,98,.08)' : 'rgba(86,227,154,.08)',
                color: busy ? COLORS.amber : COLORS.greenSoft,
              }}
            >
              {busy ? `${updateJob.state}…` : 'Обновить и перезапустить'}
            </button>
          </div>
          {updateJob && <JobSteps job={updateJob} />}
          {updateJob && updateJob.log && updateJob.log.length > 0 && (
            <div style={{ background: COLORS.logBg, border: `1px solid ${COLORS.borderDim}`, borderRadius: 7, padding: '10px 13px', ...mono, fontSize: 11.5, lineHeight: 1.65, color: '#8fa0b0', whiteSpace: 'pre-wrap', maxHeight: 200, overflowY: 'auto' }}>
              {updateJob.log.join('\n')}
            </div>
          )}
          {updateJob && updateJob.state === 'failed' && (
            <div style={{ background: 'rgba(255,107,107,.06)', border: '1px solid rgba(255,107,107,.3)', borderRadius: 7, padding: '11px 13px' }}>
              <div style={{ ...mono, fontSize: 11.5, color: COLORS.redText }}>{updateJob.error}</div>
            </div>
          )}
        </div>
      ) : (
        <div style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.border}`, borderRadius: 9, padding: '13px 18px', ...mono, fontSize: 11.5, color: COLORS.dim2 }}>
          Обновление этой ноды доступно только с самой машины node-{node.node_id.split('-')[1]} — запусти на ней дашборд.
        </div>
      )}

      <div style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.border}`, borderRadius: 9, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 11 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <div style={{ fontWeight: 600, fontSize: 13.5, color: COLORS.textHeader }}>
            Логи <span style={{ ...mono, fontSize: 11, color: COLORS.dim2, fontWeight: 400 }}>
              {isLocal ? 'node_agent.log (локально)' : `${node.host}:${node.port}/debug/log`}
            </span>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            {['все', 'node_agent', 'entry', 'middle', 'final'].map((c) => (
              <button
                key={c}
                onClick={() => setLogFilter(c)}
                style={{
                  padding: '4px 11px', borderRadius: 5, ...mono, fontSize: 11, cursor: 'pointer',
                  border: `1px solid ${logFilter === c ? 'rgba(86,227,154,.5)' : '#253141'}`,
                  background: logFilter === c ? 'rgba(86,227,154,.12)' : 'transparent',
                  color: logFilter === c ? COLORS.greenText : COLORS.dim,
                }}
              >
                {c}
              </button>
            ))}
          </div>
        </div>
        {!online && <div style={{ ...mono, fontSize: 11, color: COLORS.amber }}>нет связи с node_agent — показаны последние полученные логи</div>}
        {remoteError && <div style={{ ...mono, fontSize: 11, color: COLORS.red }}>ошибка запроса: {remoteError}</div>}
        <div ref={logRef} style={{ background: COLORS.logBg, border: `1px solid ${COLORS.borderDim}`, borderRadius: 7, padding: '11px 14px', height: 340, overflowY: 'auto', ...mono, fontSize: 11.5, lineHeight: 1.7 }}>
          {isLocal
            ? localLines.map((l, i) => <div key={i} style={{ color: COLORS.faint, whiteSpace: 'pre-wrap' }}>{l}</div>)
            : <div style={{ color: COLORS.faint, whiteSpace: 'pre-wrap' }}>{remoteLog}</div>}
        </div>
      </div>
    </div>
  );
}
