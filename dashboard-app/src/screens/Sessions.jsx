import React, { useEffect, useRef, useState } from 'react';
import { COLORS, mono, roleColor } from '../theme.js';
import { SAMPLING_DEFAULTS, SESSION_PHASES } from '../api.js';

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
        {session.context?.n_ctx > 0 && (
          <span
            style={{ ...mono, fontSize: 10.5, padding: '2px 8px', borderRadius: 4, border: '1px solid #2a3542', color: COLORS.dim }}
            title={session.context.model_native_n_ctx
              ? `модель поддерживает до ${session.context.model_native_n_ctx}`
              : undefined}
          >
            ctx {session.context.n_ctx}
          </span>
        )}
        {session.sampling && Number(session.sampling.temp) > 0 && (
          <span style={{ ...mono, fontSize: 10.5, padding: '2px 8px', borderRadius: 4, border: '1px solid #2a3542', color: COLORS.dim }}>
            temp {session.sampling.temp}
            {Number(session.sampling.top_p) < 1 ? ` · top_p ${session.sampling.top_p}` : ''}
            {session.sampling.seed !== '' ? ` · seed ${session.sampling.seed}` : ''}
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

      <ChatPanel session={session} genState={genState} onGenerate={onGenerate} />
    </div>
  );
}

function ChatBubble({ role, content }) {
  const isUser = role === 'user';
  return (
    <div style={{ display: 'flex', justifyContent: isUser ? 'flex-end' : 'flex-start' }}>
      <div
        style={{
          maxWidth: '86%',
          background: isUser ? 'rgba(86,227,154,.10)' : COLORS.logBg,
          border: `1px solid ${isUser ? 'rgba(86,227,154,.28)' : COLORS.borderDim}`,
          borderRadius: 9,
          padding: '9px 12px',
          fontSize: 12.5,
          lineHeight: 1.6,
          color: isUser ? '#d6e6dd' : '#c6d2dc',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
        }}
      >
        {content}
      </div>
    </div>
  );
}

function ChatPanel({ session, genState, onGenerate }) {
  const scrollRef = useRef(null);
  const messages = genState.messages || [];

  // Follow the tail as turns arrive, the way a chat window is expected to.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length, genState.running]);

  const send = () => onGenerate(session.session_id);
  const onKeyDown = (e) => {
    // Enter sends, Shift+Enter makes a newline.
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const t = genState.lastTiming;

  // The context is shared by the prompt and the reply, so the whole
  // conversation competes with max_tokens for it. Warn rather than clamp:
  // a long chat can exceed it without any single setting being wrong, and
  // silently rewriting the user's number would hide that.
  const nCtx = session.context?.n_ctx || 0;
  const overTokenBudget = nCtx > 0 && Number(genState.maxTokens || 64) > nCtx;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div
        ref={scrollRef}
        style={{
          display: 'flex', flexDirection: 'column', gap: 8,
          maxHeight: 340, overflowY: 'auto', paddingRight: 4, minHeight: messages.length ? 0 : 60,
        }}
      >
        {messages.length === 0 && !genState.running && (
          <div style={{ ...mono, fontSize: 11.5, color: COLORS.dim3, textAlign: 'center', padding: '18px 0' }}>
            Диалог пуст — история отправляется целиком при каждом запросе
          </div>
        )}
        {messages.map((m, i) => (
          <ChatBubble key={i} role={m.role} content={m.content} />
        ))}
        {genState.running && (
          <div style={{ ...mono, fontSize: 11.5, color: COLORS.dim, padding: '4px 2px' }}>
            Генерирую…
          </div>
        )}
      </div>

      {genState.error && <div style={{ ...mono, fontSize: 11.5, color: COLORS.red }}>{genState.error}</div>}

      {overTokenBudget && (
        <div style={{ ...mono, fontSize: 10.5, color: COLORS.amber, lineHeight: 1.5 }}>
          max_tokens больше контекста сессии ({nCtx}). Генерация оборвётся, когда
          промпт вместе с ответом его исчерпает.
        </div>
      )}

      <textarea
        value={genState.prompt}
        onChange={(e) => genState.setPrompt(session.session_id, e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Сообщение…  (Enter — отправить, Shift+Enter — перенос строки)"
        rows={2}
        style={{
          background: COLORS.inputBg, border: `1px solid ${COLORS.border}`, borderRadius: 7,
          padding: '10px 12px', color: COLORS.text, ...mono, fontSize: 12, resize: 'vertical', outline: 'none',
        }}
      />

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <button
          onClick={send}
          disabled={genState.running || !(genState.prompt || '').trim()}
          style={{
            padding: '7px 16px', borderRadius: 6, fontSize: 12.5, fontWeight: 500,
            cursor: genState.running || !(genState.prompt || '').trim() ? 'default' : 'pointer',
            border: '1px solid rgba(86,227,154,.5)',
            background: genState.running ? 'rgba(86,227,154,.06)' : 'rgba(86,227,154,.12)',
            color: COLORS.greenText, opacity: genState.running ? 0.6 : 1,
          }}
        >
          {genState.running ? 'Генерирую…' : 'Отправить'}
        </button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 11, color: overTokenBudget ? COLORS.amber : COLORS.dim, ...mono }}>
          max_tokens
          <input
            value={genState.maxTokens ?? 64}
            onChange={(e) => genState.setMaxTokens(session.session_id, e.target.value)}
            type="number"
            min={1}
            max={nCtx || undefined}
            style={{
              width: 80, background: COLORS.inputBg, borderRadius: 6, padding: '5px 8px',
              border: `1px solid ${overTokenBudget ? COLORS.amber : COLORS.border}`,
              color: COLORS.text, ...mono, fontSize: 12, outline: 'none',
            }}
          />
          {nCtx > 0 && (
            <span style={{ fontSize: 10.5, color: COLORS.dim3 }}>
              из {nCtx} ctx
            </span>
          )}
        </label>
        {messages.length > 0 && (
          <button
            onClick={() => genState.clearChat(session.session_id)}
            style={{
              marginLeft: 'auto', padding: '5px 11px', borderRadius: 6, fontSize: 11,
              cursor: 'pointer', border: `1px solid ${COLORS.border}`, background: 'transparent',
              color: COLORS.dim, ...mono,
            }}
          >
            очистить
          </button>
        )}
      </div>

      {t && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {[
            ['tok/s', t.decode_tokens_per_sec?.toFixed(1)],
            ['prefill', t.prefill_ms != null ? `${t.prefill_ms.toFixed(0)} ms` : null],
            ['tokens', t.generated_tokens],
            ['speculative', String(t.speculative ?? 'false')],
          ].map(([k, v]) => (
            <span key={k} style={{ ...mono, fontSize: 11, padding: '4px 10px', borderRadius: 5, border: '1px solid #253141', background: '#101720', color: '#8fa0b0' }}>
              {k} <span style={{ color: COLORS.green }}>{v ?? '—'}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// Compact labelled number input, matching the form's existing mono/dark style.
function ParamField({ label, value, onChange, step, min, max, placeholder, width = 78 }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 11, color: COLORS.dim }}>
      {label}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        type="number"
        step={step}
        min={min}
        max={max}
        placeholder={placeholder}
        style={{
          width, background: COLORS.inputBg, border: `1px solid ${COLORS.border}`, borderRadius: 6,
          padding: '6px 8px', color: COLORS.text, ...mono, fontSize: 12, outline: 'none',
        }}
      />
    </label>
  );
}

// Phase-by-phase view of a session being created. The orchestrator does this
// work inside one blocking call, so phases are polled separately by correlation
// id rather than streamed from the response.
function CreateProgress({ progress }) {
  const activeIdx = SESSION_PHASES.findIndex(([key]) => key === progress.phase);
  const elapsed = progress.elapsed_ms != null ? (progress.elapsed_ms / 1000).toFixed(1) : null;

  return (
    <div style={{
      background: COLORS.logBg, border: `1px solid ${COLORS.borderDim}`, borderRadius: 8,
      padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 12.5, color: COLORS.textHeader, fontWeight: 500 }}>
          Создание сессии
        </span>
        {elapsed && (
          <span style={{ ...mono, fontSize: 11, color: COLORS.dim3, marginLeft: 'auto' }}>
            {elapsed}s
          </span>
        )}
      </div>

      {SESSION_PHASES.filter(([key]) => key !== 'ready').map(([key, label], i) => {
        const isDone = activeIdx > i || progress.phase === 'ready';
        const isActive = activeIdx === i;
        // draft_fetch only happens for speculative sessions; don't imply it
        // was skipped in error when it never applied.
        const dim = !isDone && !isActive;
        return (
          <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 9, fontSize: 11.5 }}>
            <span style={{
              width: 13, textAlign: 'center', ...mono, fontSize: 11,
              color: isDone ? COLORS.green : isActive ? COLORS.greenText : COLORS.dim3,
            }}>
              {isDone ? '✓' : isActive ? '▸' : '·'}
            </span>
            <span style={{ color: dim ? COLORS.dim3 : isActive ? COLORS.text : COLORS.dim }}>
              {label}
            </span>
            {isActive && progress.total > 0 && (
              <span style={{ ...mono, fontSize: 10.5, color: COLORS.dim3 }}>
                {progress.step}/{progress.total}
              </span>
            )}
            {isActive && progress.detail && (
              <span style={{ ...mono, fontSize: 10.5, color: COLORS.dim3, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {progress.detail}
              </span>
            )}
          </div>
        );
      })}

      {progress.failed && (
        <div style={{ ...mono, fontSize: 11, color: COLORS.red }}>{progress.error}</div>
      )}
    </div>
  );
}

export default function Sessions({ sessions, models, onCreate, onDestroy, onGenerate, genStates, createProgress }) {
  const [newModel, setNewModel] = useState('');
  const [draftUrl, setDraftUrl] = useState('');
  const [draftK, setDraftK] = useState('4');
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(null);
  const [sampling, setSampling] = useState(SAMPLING_DEFAULTS);
  const [showSampling, setShowSampling] = useState(false);

  const installed = models.filter((m) => m.status && m.status !== 'discovered');
  const setParam = (key) => (value) => setSampling((prev) => ({ ...prev, [key]: value }));
  const greedy = Number(sampling.temp) <= 0;

  const create = async () => {
    if (!newModel) return;
    setCreating(true);
    setCreateError(null);
    try {
      await onCreate({
        model: newModel,
        speculativeDraftModelUrl: draftUrl,
        speculativeDraftK: draftK,
        sampling,
      });
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
            genState={genStates[s.session_id] || {
              prompt: '', running: false, messages: [],
              setPrompt: () => {}, setMaxTokens: () => {}, clearChat: () => {},
            }}
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

        <div style={{ borderTop: `1px solid ${COLORS.borderDim}`, paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <button
            onClick={() => setShowSampling((v) => !v)}
            style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: 0, border: 'none',
              background: 'transparent', cursor: 'pointer', color: COLORS.dim, fontSize: 11.5, ...mono,
            }}
          >
            <span style={{ color: COLORS.dim3 }}>{showSampling ? '▾' : '▸'}</span>
            сэмплинг
            <span style={{
              padding: '2px 8px', borderRadius: 4, fontSize: 10.5,
              border: `1px solid ${greedy ? '#2a3542' : 'rgba(86,227,154,.4)'}`,
              color: greedy ? COLORS.dim3 : COLORS.green,
            }}>
              {greedy ? 'greedy' : `temp ${sampling.temp}`}
            </span>
          </button>

          {showSampling && (
            <>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <ParamField label="temperature" value={sampling.temp} onChange={setParam('temp')} step="0.05" min="0" />
                <ParamField label="top_k" value={sampling.top_k} onChange={setParam('top_k')} step="1" min="0" />
                <ParamField label="top_p" value={sampling.top_p} onChange={setParam('top_p')} step="0.05" min="0" max="1" />
                <ParamField label="min_p" value={sampling.min_p} onChange={setParam('min_p')} step="0.01" min="0" max="1" />
                <ParamField label="repeat_penalty" value={sampling.repeat_penalty} onChange={setParam('repeat_penalty')} step="0.05" min="0" width={96} />
                <ParamField label="seed" value={sampling.seed} onChange={setParam('seed')} step="1" placeholder="random" width={96} />
              </div>
              <div style={{ ...mono, fontSize: 10.5, color: COLORS.dim3, lineHeight: 1.5 }}>
                {greedy
                  ? 'temperature 0 — жадный выбор, вывод детерминирован; остальные параметры не применяются'
                  : 'параметры фиксируются при создании сессии — воркер строит цепочку один раз при запуске'}
              </div>
              <button
                onClick={() => setSampling(SAMPLING_DEFAULTS)}
                style={{
                  alignSelf: 'flex-start', padding: '4px 10px', borderRadius: 5, fontSize: 11,
                  cursor: 'pointer', border: `1px solid ${COLORS.border}`, background: 'transparent',
                  color: COLORS.dim, ...mono,
                }}
              >
                сбросить
              </button>
            </>
          )}
        </div>

        <button
          onClick={create}
          disabled={!newModel || creating}
          style={{ padding: '8px 14px', borderRadius: 6, fontSize: 12.5, fontWeight: 500, cursor: !newModel || creating ? 'default' : 'pointer', border: '1px solid rgba(86,227,154,.5)', background: 'rgba(86,227,154,.12)', color: COLORS.greenText }}
        >
          {creating ? 'Создаю…' : 'Создать сессию'}
        </button>
        {creating && createProgress && <CreateProgress progress={createProgress} />}
        {createError && <div style={{ ...mono, fontSize: 11, color: COLORS.red }}>{createError}</div>}
        <div style={{ ...mono, fontSize: 10.5, color: COLORS.dim3 }}>layout по нодам выберет оркестратор по score</div>
      </div>
    </div>
  );
}
