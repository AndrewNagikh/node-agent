// Electron main process. This app IS this machine's node: launching it
// spawns node_agent as a child process (replacing run-agent.sh/.ps1), and
// the "update and restart" action only ever acts on that local child --
// never on another machine over the network.

const { app, BrowserWindow, ipcMain } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const IS_WIN = process.platform === 'win32';
const CONFIG_PATH = path.join(app.getPath('userData'), 'dashboard-config.json');

// --- nodes.conf (shared with run-agent.sh/.ps1) -----------------------

function parseNodesConf() {
  const confPath = path.join(REPO_ROOT, 'nodes.conf');
  const out = {};
  if (!fs.existsSync(confPath)) {
    return out;
  }
  for (const line of fs.readFileSync(confPath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq < 0) continue;
    out[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
  }
  return out;
}

function loadDashboardConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  } catch {
    return null;
  }
}

function saveDashboardConfig(cfg) {
  fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2));
}

// Resolves this machine's node id, host, port, and orchestrator URL from
// (in order): saved dashboard config, nodes.conf, sane defaults. NODE_ID
// itself still has to be chosen once per machine -- there's no way to
// infer "which of node-a/b/c am I" automatically.
function resolveConfig() {
  const saved = loadDashboardConfig();
  const conf = parseNodesConf();
  const nodeId = saved?.nodeId || process.env.NODE_ID || null;
  const orchestrator =
    saved?.orchestrator ||
    process.env.ORCHESTRATOR ||
    (conf.ORCHESTRATOR_HOST ? `http://${conf.ORCHESTRATOR_HOST}:${conf.ORCHESTRATOR_PORT || 9000}` : '');

  let host = saved?.advertiseHost || process.env.ADVERTISE_HOST || '';
  let port = saved?.port ? Number(saved.port) : Number(process.env.PORT || 0);
  if (nodeId) {
    const prefix = nodeId.toUpperCase().replace(/-/g, '_'); // node-a -> NODE_A
    if (!host && conf[`${prefix}_HOST`]) host = conf[`${prefix}_HOST`];
    if (!port && conf[`${prefix}_PORT`]) port = Number(conf[`${prefix}_PORT`]);
  }
  return { nodeId, orchestrator, host, port, modelsDir: saved?.modelsDir || '' };
}

// --- node_agent binary + spawn -----------------------------------------

function findNodeAgentBinary() {
  const buildBin = path.join(REPO_ROOT, 'llama.cpp', 'build', 'bin');
  const candidates = IS_WIN
    ? [path.join(buildBin, 'node_agent.exe'), path.join(buildBin, 'Release', 'node_agent.exe')]
    : [path.join(buildBin, 'node_agent')];
  return candidates.find((p) => fs.existsSync(p)) || null;
}

let agentProcess = null;
let agentLogBuf = [];
const AGENT_LOG_MAX_LINES = 2000;

function appendAgentLog(line) {
  agentLogBuf.push(line);
  if (agentLogBuf.length > AGENT_LOG_MAX_LINES) {
    agentLogBuf = agentLogBuf.slice(-AGENT_LOG_MAX_LINES);
  }
  for (const w of BrowserWindow.getAllWindows()) {
    w.webContents.send('agent-log-line', line);
  }
}

function broadcast(channel, payload) {
  for (const w of BrowserWindow.getAllWindows()) {
    w.webContents.send(channel, payload);
  }
}

function startAgent(cfg) {
  if (agentProcess) return { ok: false, error: 'agent already running' };
  const bin = findNodeAgentBinary();
  if (!bin) return { ok: false, error: 'node_agent binary not found -- run an update first' };
  if (!cfg.nodeId) return { ok: false, error: 'node id not configured' };

  const args = [
    '--listen', `0.0.0.0:${cfg.port || 0}`,
    '--advertise-host', cfg.host || '',
    '--orchestrator', cfg.orchestrator || '',
    '--node-id', cfg.nodeId,
  ];
  if (cfg.modelsDir) args.push('--models-dir', cfg.modelsDir);

  appendAgentLog(`$ ${bin} ${args.join(' ')}`);
  agentProcess = spawn(bin, args, { cwd: REPO_ROOT });
  agentProcess.stdout.on('data', (d) => String(d).split('\n').filter(Boolean).forEach(appendAgentLog));
  agentProcess.stderr.on('data', (d) => String(d).split('\n').filter(Boolean).forEach(appendAgentLog));
  agentProcess.on('exit', (code, signal) => {
    appendAgentLog(`node_agent exited (code=${code} signal=${signal})`);
    agentProcess = null;
    broadcast('agent-state', { running: false, code, signal });
  });
  broadcast('agent-state', { running: true });
  return { ok: true };
}

function stopAgent() {
  return new Promise((resolve) => {
    if (!agentProcess) return resolve();
    // Capture this process, not the mutable `agentProcess` binding: by the
    // time the fallback timer below fires, startAgent() may already have
    // reassigned `agentProcess` to a brand-new child (the whole point of
    // calling stopAgent() during an update). An uncleared timer that reads
    // `agentProcess` at fire-time kills whatever is running *then* --
    // which was this exact bug: the freshly-restarted node_agent got
    // SIGTERM'd a few seconds after startup by a timer meant for the old one.
    const proc = agentProcess;
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve();
    };
    proc.once('exit', finish);
    proc.kill(IS_WIN ? undefined : 'SIGTERM');
    // Windows doesn't deliver SIGTERM to arbitrary processes; force-kill
    // after a grace period so an update never hangs waiting on it.
    const timer = setTimeout(() => {
      if (!settled) proc.kill();
      finish();
    }, 4000);
  });
}

// --- update: git pull + submodule + build, then restart the local agent -

function runCmd(cmd, args, cwd, onLine) {
  return new Promise((resolve, reject) => {
    onLine(`$ ${cmd} ${args.join(' ')}`);
    const child = spawn(cmd, args, { cwd, shell: IS_WIN });
    child.stdout.on('data', (d) => String(d).split('\n').filter(Boolean).forEach(onLine));
    child.stderr.on('data', (d) => String(d).split('\n').filter(Boolean).forEach(onLine));
    child.on('error', reject);
    child.on('exit', (code) => (code === 0 ? resolve() : reject(new Error(`${cmd} exited ${code}`))));
  });
}

async function runUpdate() {
  const emit = (state, line) => {
    if (line) appendAgentLog(line);
    broadcast('update-progress', { state, line });
  };
  try {
    emit('pulling');
    await runCmd('git', ['pull', 'origin', 'main'], REPO_ROOT, (l) => emit('pulling', l));
    await runCmd('git', ['submodule', 'update', '--recursive'], REPO_ROOT, (l) => emit('pulling', l));

    emit('building');
    if (IS_WIN) {
      await runCmd('powershell.exe', ['-ExecutionPolicy', 'Bypass', '-File', 'build.ps1', 'agents'], REPO_ROOT, (l) =>
        emit('building', l),
      );
    } else {
      await runCmd('./build.sh', ['agents'], REPO_ROOT, (l) => emit('building', l));
    }

    emit('restarting');
    await stopAgent();
    const cfg = resolveConfig();
    const res = startAgent(cfg);
    if (!res.ok) throw new Error(res.error);

    emit('done');
  } catch (err) {
    emit('failed', String(err && err.message ? err.message : err));
    throw err;
  }
}

// --- IPC ------------------------------------------------------------------

ipcMain.handle('get-config', () => resolveConfig());
ipcMain.handle('save-config', (_e, cfg) => {
  saveDashboardConfig(cfg);
  return resolveConfig();
});
ipcMain.handle('get-agent-log', () => agentLogBuf);
ipcMain.handle('agent-state', () => ({ running: !!agentProcess }));
ipcMain.handle('start-agent', () => startAgent(resolveConfig()));
ipcMain.handle('run-update', async () => {
  try {
    await runUpdate();
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String(err && err.message ? err.message : err) };
  }
});

// --- window + lifecycle ----------------------------------------------------

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    backgroundColor: '#0a0e13',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  if (process.env.DASHBOARD_DEV) {
    win.loadURL('http://localhost:5834');
    win.webContents.openDevTools({ mode: 'detach' });
  } else {
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }
}

app.whenReady().then(() => {
  createWindow();
  const cfg = resolveConfig();
  if (cfg.nodeId) {
    startAgent(cfg);
  }
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', async (e) => {
  if (agentProcess) {
    e.preventDefault();
    await stopAgent();
    app.quit();
  }
});
