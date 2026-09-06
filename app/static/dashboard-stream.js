import { setChartsVisible } from './dashboard-charts.js';
import { fetchDbSize } from './database-status.js';
import { renderDashboard } from './dashboard-renderer.js';
import { setText } from './ui-utils.js';

let state = {};
let renderScheduled = false;
let receivedSnapshot = false;
let dashboardActive = true;
const dirty = { realtime: false, detail: false, activity: false };

function mergePatch(patch) {
  const previousSystem = state.system;
  state = { ...state, ...patch };
  if (patch.system) state.system = { ...(previousSystem || {}), ...patch.system };
}

function markDirty(flags) {
  Object.keys(flags).forEach((key) => { dirty[key] = dirty[key] || Boolean(flags[key]); });
  if (!dashboardActive || renderScheduled) return;
  renderScheduled = true;
  const render = () => {
    renderScheduled = false;
    const flags = { ...dirty };
    Object.keys(dirty).forEach((key) => { dirty[key] = false; });
    renderDashboard(state, flags);
  };
  if (window.requestAnimationFrame) window.requestAnimationFrame(render); else window.setTimeout(render, 0);
}

function applyFrame(kind, payload) {
  mergePatch(payload);
  if (kind === 'snapshot') {
    receivedSnapshot = true;
    markDirty({ realtime: true, detail: true, activity: true });
  } else if (kind === 'detail') {
    markDirty({ detail: true, activity: true });
  } else {
    markDirty({ realtime: true });
  }
}

function parseFrame(kind, event) {
  try {
    applyFrame(kind, JSON.parse(event.data));
  } catch (error) {
    console.error(`Failed to parse dashboard ${kind} frame:`, error);
  }
}

async function hydrateFallback() {
  if (receivedSnapshot) return;
  try {
    const response = await fetch('/api/system/dashboard', { cache: 'no-store' });
    if (response.ok) applyFrame('snapshot', await response.json());
  } catch (error) {
    console.warn('Initial dashboard snapshot failed:', error);
  }
}

export function startDashboard() {
  fetchDbSize();
  if (!window.EventSource) {
    hydrateFallback();
    return;
  }
  const source = new EventSource('/api/system/stream');
  source.addEventListener('snapshot', (event) => parseFrame('snapshot', event));
  source.addEventListener('tick', (event) => parseFrame('tick', event));
  source.addEventListener('detail', (event) => parseFrame('detail', event));
  source.onmessage = (event) => parseFrame('snapshot', event); // compatible with pre-3.0 streams
  source.onerror = () => setText('conn-status', 'RECONNECTING...');
  window.setTimeout(hydrateFallback, 3000);
}

export function setDashboardActive(active) {
  dashboardActive = Boolean(active);
  setChartsVisible(dashboardActive);
  if (dashboardActive && Object.keys(state).length) {
    markDirty({ realtime: true, detail: true, activity: true });
  }
}
