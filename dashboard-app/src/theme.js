export const COLORS = {
  bg: '#0a0e13',
  panelBg: '#0e1319',
  cardBg: '#121820',
  cardBgHover: '#141b25',
  headerBg: '#0d1218',
  logBg: '#0a0e13',
  inputBg: '#0a0e13',
  border: '#1f2934',
  borderDim: '#1c242e',
  navHover: '#16202b',
  navActive: '#182430',
  text: '#d7e0e8',
  textBright: '#e8f2ea',
  textHeader: '#e8f0f6',
  dim: '#7d8b99',
  dim2: '#5c6b7a',
  dim3: '#4a5866',
  faint: '#a9b8c6',
  green: '#56e39a',
  greenText: '#7bebac',
  greenSoft: '#8fd9b0',
  red: '#ff6b6b',
  redText: '#ff9b9b',
  amber: '#ffc862',
  blue: '#6ab7ff',
};

export const mono = { fontFamily: "'JetBrains Mono', monospace" };
export const sans = { fontFamily: "'IBM Plex Sans', sans-serif" };

export function alpha(hex, suffix) {
  return hex + suffix;
}

export const roleColor = (role) => {
  if (role === 'entry') return COLORS.green;
  if (role === 'middle') return COLORS.blue;
  if (role === 'final') return COLORS.amber;
  return COLORS.dim;
};

export function fmtBytes(mb) {
  if (mb == null) return '—';
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`;
  return `${Math.round(mb)} MB`;
}

export function fmtAgo(epochSeconds) {
  if (!epochSeconds) return '—';
  const diff = Date.now() / 1000 - epochSeconds;
  if (diff < 5) return 'сейчас';
  if (diff < 60) return `${Math.floor(diff)}с назад`;
  if (diff < 3600) return `${Math.floor(diff / 60)}м назад`;
  return `${Math.floor(diff / 3600)}ч назад`;
}
