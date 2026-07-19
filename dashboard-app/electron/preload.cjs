const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('dashboard', {
  getConfig: () => ipcRenderer.invoke('get-config'),
  saveConfig: (cfg) => ipcRenderer.invoke('save-config', cfg),
  getAgentLog: () => ipcRenderer.invoke('get-agent-log'),
  getAgentState: () => ipcRenderer.invoke('agent-state'),
  startAgent: () => ipcRenderer.invoke('start-agent'),
  runUpdate: () => ipcRenderer.invoke('run-update'),
  onAgentLogLine: (cb) => {
    const handler = (_e, line) => cb(line);
    ipcRenderer.on('agent-log-line', handler);
    return () => ipcRenderer.off('agent-log-line', handler);
  },
  onAgentState: (cb) => {
    const handler = (_e, state) => cb(state);
    ipcRenderer.on('agent-state', handler);
    return () => ipcRenderer.off('agent-state', handler);
  },
  onUpdateProgress: (cb) => {
    const handler = (_e, payload) => cb(payload);
    ipcRenderer.on('update-progress', handler);
    return () => ipcRenderer.off('update-progress', handler);
  },
});
