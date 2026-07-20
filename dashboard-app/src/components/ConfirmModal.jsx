import React from 'react';
import { COLORS, mono } from '../theme.js';

export default function ConfirmModal({ confirm, onCancel, onConfirm }) {
  if (!confirm) return null;
  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: 'rgba(4,8,12,.72)', backdropFilter: 'blur(2px)',
        zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      <div style={{ width: 440, background: '#131a23', border: `1px solid #2a3542`, borderRadius: 10, padding: '20px 22px', boxShadow: '0 18px 50px rgba(0,0,0,.6)' }}>
        <div style={{ fontWeight: 600, fontSize: 15, color: COLORS.textHeader, marginBottom: 9 }}>{confirm.title}</div>
        <div style={{ fontSize: 12.5, lineHeight: 1.55, color: '#aeb9c4' }}>{confirm.body}</div>
        {confirm.warn && (
          <div style={{ marginTop: 10, fontSize: 12, color: COLORS.amber, background: 'rgba(255,200,98,.07)', border: '1px solid rgba(255,200,98,.3)', borderRadius: 6, padding: '8px 11px' }}>
            ⚠ {confirm.warn}
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9, marginTop: 18 }}>
          <button
            onClick={onCancel}
            style={{ padding: '7px 15px', borderRadius: 6, fontSize: 12.5, cursor: 'pointer', border: '1px solid #2a3542', background: 'transparent', color: '#aeb9c4' }}
          >
            Отмена
          </button>
          <button
            onClick={onConfirm}
            style={{ padding: '7px 15px', borderRadius: 6, fontSize: 12.5, fontWeight: 500, cursor: 'pointer', border: '1px solid #e05252', background: '#c94747', color: '#fff', ...mono }}
          >
            {confirm.okLabel || 'Подтвердить'}
          </button>
        </div>
      </div>
    </div>
  );
}
