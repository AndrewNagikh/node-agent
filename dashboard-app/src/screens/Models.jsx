import React, { useState } from 'react';
import { COLORS, mono } from '../theme.js';
import { searchHuggingFace, fetchHuggingFaceFiles, registerModel } from '../api.js';

const STATUS_COLOR = {
  ready: COLORS.green,
  installed: COLORS.green,
  manifest_ready: COLORS.blue,
  installing: COLORS.amber,
  discovered: COLORS.dim,
};

// Verdicts come from the orchestrator and are estimates from file size alone
// (see hf_fit_estimate in orchestrator.cpp). Labelled as estimates in the UI so
// they aren't read as the planner's real answer.
const FIT = {
  fits: { color: COLORS.green, label: 'влезет' },
  tight: { color: COLORS.amber, label: 'впритык' },
  no: { color: COLORS.redText, label: 'не влезет' },
  unknown: { color: COLORS.dim3, label: 'нет данных' },
};

// In practice a GGUF filename ends with its quant (Model-Name-Q4_K_M.gguf).
// Fall back to the full name when it doesn't match rather than show a
// misleading fragment.
function quantLabel(filename) {
  const base = filename.replace(/\.gguf$/i, '');
  const parts = base.split('-');
  const tail = parts[parts.length - 1];
  return /^(I?Q\d|BF16|F16|F32)/i.test(tail) ? tail : base;
}

function modelIdFor(filename) {
  return filename.replace(/\.gguf$/i, '').toLowerCase().replace(/[^a-z0-9._-]+/g, '-');
}

function FileRow({ file, repository, onRegister }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);
  const fit = FIT[file.fit?.verdict] || FIT.unknown;

  const add = async () => {
    setBusy(true);
    setError(null);
    try {
      await onRegister({ repository, filename: file.filename });
      setDone(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', flexWrap: 'wrap' }}>
      <span style={{ ...mono, fontSize: 11.5, color: COLORS.text, minWidth: 92 }}>
        {quantLabel(file.filename)}
      </span>
      <span style={{ ...mono, fontSize: 11, color: COLORS.dim }}>
        {file.fit?.file_gb?.toFixed(2)} GB
      </span>
      <span style={{
        ...mono, fontSize: 10.5, padding: '2px 8px', borderRadius: 4,
        border: `1px solid ${fit.color}55`, color: fit.color,
      }}>
        {fit.label}
      </span>
      {error && <span style={{ ...mono, fontSize: 10.5, color: COLORS.red }}>{error}</span>}
      <button
        onClick={add}
        disabled={busy || done}
        style={{
          marginLeft: 'auto', padding: '4px 11px', borderRadius: 5, fontSize: 11, ...mono,
          cursor: busy || done ? 'default' : 'pointer',
          border: `1px solid ${done ? '#2a3542' : 'rgba(86,227,154,.45)'}`,
          background: done ? 'transparent' : 'rgba(86,227,154,.10)',
          color: done ? COLORS.dim3 : COLORS.greenText,
        }}
      >
        {done ? 'в реестре' : busy ? '…' : 'добавить'}
      </button>
    </div>
  );
}

function RepoCard({ repo, orchestrator, onRegister }) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const toggle = async () => {
    const next = !open;
    setOpen(next);
    if (next && !files && !loading) {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchHuggingFaceFiles(orchestrator, repo.repository);
        setFiles(data.files || []);
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    }
  };

  return (
    <div style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.border}`, borderRadius: 9, padding: '11px 14px' }}>
      <button
        onClick={toggle}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%', padding: 0,
          border: 'none', background: 'transparent', cursor: 'pointer', textAlign: 'left',
        }}
      >
        <span style={{ color: COLORS.dim3, ...mono, fontSize: 11 }}>{open ? '▾' : '▸'}</span>
        <span style={{ ...mono, fontSize: 12.5, color: COLORS.textBright, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {repo.repository}
        </span>
        <span style={{ ...mono, fontSize: 10.5, color: COLORS.dim3 }}>
          ↓{repo.downloads?.toLocaleString?.() ?? repo.downloads}
        </span>
      </button>

      {open && (
        <div style={{ marginTop: 8, paddingTop: 8, borderTop: `1px solid ${COLORS.borderDim}` }}>
          {loading && <div style={{ ...mono, fontSize: 11.5, color: COLORS.dim }}>Загружаю список файлов…</div>}
          {error && <div style={{ ...mono, fontSize: 11.5, color: COLORS.red }}>{error}</div>}
          {files && files.length === 0 && (
            <div style={{ ...mono, fontSize: 11.5, color: COLORS.dim3 }}>GGUF-файлов не найдено</div>
          )}
          {files && files.map((f) => (
            <FileRow key={f.filename} file={f} repository={repo.repository} onRegister={onRegister} />
          ))}
        </div>
      )}
    </div>
  );
}

function HuggingFaceBrowser({ orchestrator, onRegister }) {
  const [query, setQuery] = useState('');
  const [repos, setRepos] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const search = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await searchHuggingFace(orchestrator, query);
      setRepos(data.models || []);
    } catch (e) {
      setError(e.message);
      setRepos(null);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 8 }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
          placeholder="Поиск GGUF-моделей на Hugging Face…"
          style={{
            flex: 1, background: COLORS.inputBg, border: `1px solid ${COLORS.border}`,
            borderRadius: 7, padding: '8px 12px', color: COLORS.text, ...mono, fontSize: 12, outline: 'none',
          }}
        />
        <button
          onClick={search}
          disabled={loading}
          style={{
            padding: '8px 16px', borderRadius: 6, fontSize: 12.5, ...mono,
            cursor: loading ? 'default' : 'pointer',
            border: '1px solid rgba(86,227,154,.5)', background: 'rgba(86,227,154,.12)',
            color: COLORS.greenText,
          }}
        >
          {loading ? '…' : 'Найти'}
        </button>
      </div>

      <div style={{ ...mono, fontSize: 10.5, color: COLORS.dim3, lineHeight: 1.5 }}>
        Оценка влезаемости приблизительная — только по размеру файла. Точный расчёт
        (KV-кэш, буферы, раскладка по нодам) выполняется после добавления в реестр,
        на этапе manifest/layout.
      </div>

      {error && <div style={{ ...mono, fontSize: 11.5, color: COLORS.red }}>{error}</div>}
      {repos && repos.length === 0 && (
        <div style={{ ...mono, fontSize: 12, color: COLORS.dim2 }}>Ничего не найдено.</div>
      )}
      {repos && repos.map((r) => (
        <RepoCard key={r.repository} repo={r} orchestrator={orchestrator} onRegister={onRegister} />
      ))}
    </div>
  );
}

export default function Models({ models, orchestrator }) {
  const [tab, setTab] = useState('installed');

  const addFromHuggingFace = async ({ repository, filename }) => {
    await registerModel(orchestrator, { modelId: modelIdFor(filename), repository, filename });
  };

  const tabStyle = (key) => ({
    padding: '6px 14px', borderRadius: 6, fontSize: 12, ...mono, cursor: 'pointer',
    border: `1px solid ${tab === key ? 'rgba(86,227,154,.45)' : COLORS.border}`,
    background: tab === key ? 'rgba(86,227,154,.10)' : 'transparent',
    color: tab === key ? COLORS.greenText : COLORS.dim,
  });

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 900 }}>
      <div style={{ display: 'flex', gap: 8 }}>
        <button style={tabStyle('installed')} onClick={() => setTab('installed')}>
          В кластере ({models.length})
        </button>
        <button style={tabStyle('hf')} onClick={() => setTab('hf')}>
          Hugging Face
        </button>
      </div>

      {tab === 'installed' ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {models.length === 0 && (
            <div style={{ ...mono, fontSize: 12, color: COLORS.dim2 }}>Реестр моделей пуст.</div>
          )}
          {models.map((m) => {
            const col = STATUS_COLOR[m.status] || COLORS.dim;
            return (
              <div key={m.model_id} style={{ background: COLORS.cardBg, border: `1px solid ${COLORS.border}`, borderRadius: 9, padding: '13px 16px', display: 'flex', alignItems: 'center', gap: 14 }}>
                <span style={{ ...mono, fontWeight: 700, fontSize: 13, color: COLORS.textBright, flex: 1 }}>{m.model_id}</span>
                <span style={{ ...mono, fontSize: 11.5, color: COLORS.dim }}>{m.architecture || ''}</span>
                <span style={{ ...mono, fontSize: 11, padding: '3px 10px', borderRadius: 4, border: `1px solid ${col}55`, color: col }}>{m.status}</span>
              </div>
            );
          })}
          <div style={{ ...mono, fontSize: 11, color: COLORS.dim3, padding: '4px 2px' }}>GET /models · poll 5s</div>
        </div>
      ) : (
        <HuggingFaceBrowser orchestrator={orchestrator} onRegister={addFromHuggingFace} />
      )}
    </div>
  );
}
