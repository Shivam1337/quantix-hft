import { memo } from 'preact/compat';
import { useDatabaseSize } from './database-store.js';
import { duration } from '../lib/format.js';

function DatabaseBadge() {
  const [database, refresh] = useDatabaseSize();
  return (
    <div
      class="db-storage-box mono"
      title="Total database disk footprint. Refresh queries the server once; it does not poll."
    >
      <div class="db-badge-info">
        <span class="db-badge-title">DB ({database.backend}):</span>
        <span class="db-badge-size">{database.formatted}</span>
      </div>
      <button
        class="db-refresh-btn"
        disabled={database.loading}
        onClick={refresh}
        title="Query database size now"
      >
        {database.loading ? '⌛' : '🔄'}
      </button>
      <span class="db-badge-title" style={{ color: 'var(--text-dim)', marginLeft: '2px' }}>
        {database.checkedAt ? `@ ${database.checkedAt}` : ''}
      </span>
    </div>
  );
}

function Header({ state }) {
  const system = state.system || {};
  const stream = state.stream || {};
  const uptime = system.uptime_formatted || duration(system.uptime_seconds);
  const feedStatus = system.status || stream.status || 'INITIALIZING';
  return (
    <header class="divided-header">
      <div class="command-market-strip">
        <div class="asset-symbol-pill mono"><span class="symbol-dot" /><span class="symbol-name">BTC-USDT PERP</span></div>
        <div class="route-tag mono">
          <span>5 DISCOVERY FEEDS</span><span class="route-arrow">→</span>
          <span class="target-name" style={{ color: 'var(--lighter-color)', fontWeight: 700 }}>LIGHTER.XYZ (ZK)</span>
        </div>
        <span class="zero-fee-pill mono">0% MAKER / TAKER FEES</span>
      </div>
      <div class="header-status-strip">
        <DatabaseBadge />
        <div class="header-stat-item">
          <span class="stat-tag">Feed Stream</span>
          <span class="stat-text"><span class="dot" />{feedStatus} · {system.streaming_feeds ?? 0}/{system.total_feeds ?? 6} FEEDS · {system.tick_rate_hz ?? 0} Hz</span>
        </div>
        <div class="header-stat-item"><span class="stat-tag">Sync Time</span><span class="stat-text">{state.updated_at || '--:--:--'}</span></div>
        <div class="header-stat-item"><span class="stat-tag">Server Uptime</span><span class="stat-text" style={{ color: '#94a3b8' }}>{uptime}</span></div>
      </div>
    </header>
  );
}

export const DashboardHeader = memo(Header);
