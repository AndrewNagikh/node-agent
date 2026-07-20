// Dev-only fallback for window.dashboard when this app is opened in a
// plain browser tab instead of the real Electron shell (no IPC bridge
// there). Only the Electron-specific bits (local process control) are
// mocked; everything else in the app talks to the real orchestrator/
// node_agent over fetch(), which works fine from a plain tab now that
// CORS is enabled on those servers.
if (typeof window !== 'undefined' && !window.dashboard) {
  let cfg = null;
  const logLines = [];
  const listeners = { log: [], state: [], update: [] };

  window.dashboard = {
    getConfig: async () => cfg,
    saveConfig: async (next) => {
      cfg = { nodeId: next.nodeId, orchestrator: next.orchestrator || '', host: '', port: 0, modelsDir: '' };
      return cfg;
    },
    getAgentLog: async () => logLines,
    getAgentState: async () => ({ running: false }),
    startAgent: async () => ({ ok: false, error: 'preview mode (no Electron) -- local agent control disabled' }),
    runUpdate: async () => ({ ok: false, error: 'preview mode (no Electron) -- local agent control disabled' }),
    onAgentLogLine: (cb) => { listeners.log.push(cb); return () => {}; },
    onAgentState: (cb) => { listeners.state.push(cb); return () => {}; },
    onUpdateProgress: (cb) => { listeners.update.push(cb); return () => {}; },
  };
}
