import React from 'react';
import { COLORS, mono } from '../theme.js';

const STATUS_COLOR = {
  ready: COLORS.green,
  installed: COLORS.green,
  manifest_ready: COLORS.blue,
  installing: COLORS.amber,
  discovered: COLORS.dim,
};

export default function Models({ models }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 900 }}>
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
  );
}
