import { useDatabaseSize } from '../dashboard/database-store.js';
import { useDashboardState } from '../dashboard/store.js';
import { duration } from '../lib/format.js';

const NAV_ITEMS = [
  ['dashboard', '📊', 'Live Trading'],
  ['wallet', '👛', 'Wallet & Balances'],
  ['settings', '⚙️', 'Settings & Keys'],
  ['api', '🔍', 'API Inspector'],
];

export function Sidebar({ activeTab, onNavigate }) {
  const state = useDashboardState();
  const [database] = useDatabaseSize();
  const performance = state.trading_performance || {};
  const mode = performance.trading_mode || (performance.is_real_mode ? 'REAL' : 'SIMULATION');
  const enabled = (state.trading_enabled ?? performance.trading_enabled) !== false;
  const modeClass = !enabled ? 'mode-paused' : mode === 'REAL' ? 'mode-real' : mode === 'DUAL' ? 'mode-dual' : '';
  const modeSub = !enabled ? `${mode} · New entries blocked` : mode === 'DUAL' ? 'Live + matched simulation' : mode === 'REAL' ? 'Active On-Chain zkLighter' : 'Paper Trading (0 Risk)';
  const uptime = state.system?.uptime_formatted || duration(state.system?.uptime_seconds);
  return (
    <aside class="sidebar" id="app-sidebar">
      <div class="sidebar-brand">
        <div class="coin-badge">⚡</div>
        <div class="sidebar-brand-text"><div class="sidebar-title">QUANTIX HFT</div><div class="sidebar-sub">LEAD-LAG ENGINE</div></div>
      </div>
      <button class={`sidebar-mode-box ${modeClass}`} type="button" onClick={() => onNavigate('settings')} title="Open Settings and execution mode">
        <div class="mode-indicator-dot" />
        <div><div class="mode-indicator-label">{enabled ? mode : 'PAUSED'}</div><div class="mode-indicator-sub">{modeSub}</div></div>
      </button>
      <nav class="sidebar-nav">
        {NAV_ITEMS.map(([tab, icon, label]) => (
          <button class={`nav-item ${activeTab === tab ? 'active' : ''}`} id={`nav-${tab}`} key={tab} type="button" onClick={() => onNavigate(tab)}>
            <span class="nav-icon">{icon}</span><span class="nav-label">{label}</span>
          </button>
        ))}
      </nav>
      <div class="sidebar-footer">
        <div class="sidebar-stat-row mono"><span class="stat-tag">UPTIME</span><span>{uptime}</span></div>
        <div class="sidebar-stat-row mono"><span class="stat-tag">DB SIZE</span><span>{database.formatted}</span></div>
        <div class="sidebar-stat-row mono"><span class="stat-tag">ENGINE</span><span style={{ color: 'var(--green)' }}>ONLINE</span></div>
      </div>
    </aside>
  );
}
