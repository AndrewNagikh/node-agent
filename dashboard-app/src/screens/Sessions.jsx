import React, { useState } from 'react';
import { COLORS, mono, roleColor } from '../theme.js';

function SessionCard({ session, onDestroy, onGenerate, genState }) {
  const pipe = session.pipeline || [];
  return (
    <div style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.border}`, borderRadius: 9, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ ...mono, fontSize: 11, color: COLORS.dim2 }}>{session.session_id}</span>
        <span style={{ ...mono, fontWeight: 700, fontSize: 14, color: COLORS.textBright, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {session.model}
        </span>
        {session.speculative && (
          <span style={{ ...mono, fontSize: 10.5, padding: '2px 8px', borderRadius: 4, border: '1px solid rgba(86,227,154,.4)', color: COLORS.green }}>
            speculative k={session.draftK}
          </span>
        )}
        <button
          onClick={() => onDestroy(session.session_id)}
          style={{ marginLeft: 'auto', padding: '5px 11px', borderRadius: 6, fontSize: 11.5, cursor: 'pointer', border: '1px solid rgba(255,107,107,.4)', background: 'transparent', color: COLORS.redText }}
        >
          удалить
        </button>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {pipe.map((p, i) => (
          <span key={p.node_id} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ ...mono, fontSize: 11, padding: '4px 10px', borderRadius: 5, whiteSpace: 'nowrap', border: `1px solid ${roleColor(p.role)}55`, color: roleColor(p.role), background: `${roleColor(p.role)}0d` }}>
              {p.role} @ {p.node_id}
            </span>
            {i < pipe.length - 1 && <span style={{ color: '#3a4756', ...mono }}>→</span>}
          </span>
        ))}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <textarea
          value={genState.prompt}
          onChange={(e) => genState.setPrompt(session.session_id, e.target.value)}
          placeholder="Промпт…"
          rows={3}
          style={{ background: COLORS.inputBg, border: `1px solid ${COLORS.border}`, borderRadius: 7, padding: '10px 12px', color: COLORS.text, ...mono, fontSize: 12, resize: 'vertical', outline: 'none' }}
        />
        <div>
          <button
            onClick={() => onGenerate(session.session_id)}
            disabled={genState.running}
            style={{
              display: 'flex', alignItems: 'center', gap: 7, padding: '7px 16px', borderRadius: 6, fontSize: 12.5,
              fontWeight: 500, cursor: genState.running ? 'default' : 'pointer',
              border: '1px solid rgba(86,227,154,.5)', background: 'rgba(86,227,154,.12)', color: COLORS.greenText,
            }}
          >
            {genState.running ? 'Генерирую…' : 'Отправить'}
          </button>
        </div>
        {genState.error && <div style={{ ...mono, fontSize: 11.5, color: COLORS.red }}>{genState.error}</div>}
        {genState.result && (
          <>
            <div style={{ background: COLORS.logBg, border: `1px solid ${COLORS.borderDim}`, borderRadius: 7, padding: '12px 14px', fontSize: 12.5, lineHeight: 1.6, color: '#c6d2dc' }}>
              {genState.result.text}
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {[
                ['tok/s', genState.result.timing?.decode_tokens_per_sec?.toFixed(1)],
                ['prefill', `${genState.result.timing?.prefill_ms?.toFixed(0)} ms`],
                ['tokens', genState.result.timing?.generated_tokens],
                ['speculative', String(genState.result.timing?.speculative ?? 'false')],
              ].map(([k, v]) => (
                <span key={k} style={{ ...mono, fontSize: 11, padding: '4px 10px', borderRadius: 5, border: '1px solid #253141', background: '#101720', color: '#8fa0b0' }}>
                  {k} <span style={{ color: COLORS.green }}>{v ?? '—'}</span>
                </span>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function Sessions({ sessions, models, onCreate, onDestroy, onGenerate, genStates }) {
  const [newModel, setNewModel] = useState('');
  const [draftUrl, setDraftUrl] = useState('');
  const [draftK, setDraftK] = useState('4');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);

  const installed = models.filter((m) => m.status && m.status !== 'discovered');

  const create = async () => {
    if (!newModel) return;
    setCreating(true);
    setCreateError(null);
    try {
      await onCreate({ model: newModel, speculativeDraftModelUrl: draftUrl, speculativeDraftK: draftK });
    } catch (e) {
      setCreateError(e.message);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(430px,1fr))', gap: 16, alignItems: 'start', maxWidth: 1200 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {sessions.length === 0 && (
          <div style={{ background: COLORS.cardBg, border: '1px dashed #2a3542', borderRadius: 9, padding: 28, textAlign: 'center', color: COLORS.dim2, fontSize: 12.5 }}>
            Нет активных сессий — создайте справа
          </div>
        )}
        {sessions.map((s) => (
          <SessionCard
            key={s.session_id}
            session={s}
            onDestroy={onDestroy}
            onGenerate={onGenerate}
            genState={genStates[s.session_id] || { prompt: '', running: false, setPrompt: () => {} }}
          />
        ))}
      </div>

      <div style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.border}`, borderRadius: 9, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontWeight: 600, fontSize: 13.5, color: COLORS.textHeader }}>Новая сессия</div>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 11.5, color: COLORS.dim }}>
          модель (из реестра)
          <select
            value={newModel}
            onChange={(e) => setNewModel(e.target.value)}
            style={{ background: COLORS.inputBg, border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: '8px 10px', color: COLORS.text, ...mono, fontSize: 12, outline: 'none' }}
          >
            <option value="">— выбрать —</option>
            {installed.map((m) => (
              <option key={m.model_id} value={m.model_id}>{m.model_id}</option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 11.5, color: COLORS.dim }}>
          speculative_draft_model_url <span style={{ color: COLORS.dim3 }}>(опционально)</span>
          <input
            value={draftUrl}
            onChange={(e) => setDraftUrl(e.target.value)}
            placeholder="https://huggingface.co/.../model.gguf"
            style={{ background: COLORS.inputBg, border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: '8px 10px', color: COLORS.text, ...mono, fontSize: 12, outline: 'none' }}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 5, fontSize: 11.5, color: COLORS.dim }}>
          speculative_draft_k
          <input
            value={draftK}
            onChange={(e) => setDraftK(e.target.value)}
            type="number"
            style={{ width: 90, background: COLORS.inputBg, border: `1px solid ${COLORS.border}`, borderRadius: 6, padding: '8px 10px', color: COLORS.text, ...mono, fontSize: 12, outline: 'none' }}
          />
        </label>
        <button
          onClick={create}
          disabled={!newModel || creating}
          style={{ padding: '8px 14px', borderRadius: 6, fontSize: 12.5, fontWeight: 500, cursor: !newModel || creating ? 'default' : 'pointer', border: '1px solid rgba(86,227,154,.5)', background: 'rgba(86,227,154,.12)', color: COLORS.greenText }}
        >
          {creating ? 'Создаю…' : 'Создать сессию'}
        </button>
        {createError && <div style={{ ...mono, fontSize: 11, color: COLORS.red }}>{createError}</div>}
        <div style={{ ...mono, fontSize: 10.5, color: COLORS.dim3 }}>layout по нодам выберет оркестратор по score</div>
      </div>
    </div>
  );
}
