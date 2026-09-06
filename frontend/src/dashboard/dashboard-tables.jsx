import { memo } from 'preact/compat';
import { finite, localTime, money, number } from '../lib/format.js';

function EmptyRow({ columns, children }) {
  return <tr><td colSpan={columns} style={{ color: 'var(--text-muted)', textAlign: 'center' }}>{children}</td></tr>;
}

function Trades({ trades }) {
  if (!trades.length) return <EmptyRow columns={8}>No closed paper trades recorded yet.</EmptyRow>;
  return trades.map((trade) => {
    const margin = Number(trade.margin_allocated_usd) || 50;
    const pnl = Number(trade.net_pnl) || 0;
    const rom = (pnl / margin) * 100;
    const size = trade.size_btc ?? trade.size ?? '--';
    const notional = trade.notional_usd || Number(size) * Number(trade.entry_px || 0);
    return (
      <tr key={trade.id || `${trade.time}-${trade.side}`}>
        <td class="mono">{trade.time}</td>
        <td><strong style={{ color: trade.side === 'LONG' ? 'var(--green)' : 'var(--orange)' }}>{trade.side}</strong> <span style={{ fontSize: '9px', color: 'var(--text-muted)' }}>{trade.leader}</span></td>
        <td><span class="mono">{size} BTC</span> <span style={{ fontSize: '9px', color: 'var(--lighter-color)' }}>{money(notional, 0)} @ {trade.leverage || 50}x</span></td>
        <td class="mono">{money(trade.entry_px, 1)}</td>
        <td class="mono">{money(trade.exit_px, 1)}</td>
        <td class={`mono ${trade.is_win ? 'win-tag' : 'loss-tag'}`}>{pnl >= 0 ? '+' : ''}{money(pnl)} <span style={{ fontSize: '9px', color: 'var(--text-muted)' }}>({rom >= 0 ? '+' : ''}{number(rom, 1)}%)</span></td>
        <td class="mono">{trade.hold_sec}s</td>
        <td style={{ color: 'var(--text-muted)', fontSize: '10px' }}>{trade.reason}</td>
      </tr>
    );
  });
}

function Providers({ providers }) {
  if (!providers.length) return <EmptyRow columns={6}>Waiting for provider telemetry...</EmptyRow>;
  return providers.map((provider) => {
    const quality = String(provider.data_quality || 'WAITING').toLowerCase();
    const age = provider.age_ms == null ? '--' : `${number(provider.age_ms, 1)} ms`;
    const velocity = provider.velocity_usd_2s;
    return (
      <tr key={provider.id || provider.name}>
        <td>{provider.name || provider.id || 'Unknown provider'}<div class="provider-role">{provider.role || '--'}</div></td>
        <td class={`provider-quality provider-${quality}`} title={provider.connection_status || ''}>{provider.data_quality || 'WAITING'} · {age}</td>
        <td class="mono">{Number(provider.updates || 0).toLocaleString('en-US')}</td>
        <td class="mono">{provider.mid_price == null ? '--' : money(provider.mid_price, 1)}<div class="provider-role mono">{provider.spread == null ? 'spread --' : `spread ${money(provider.spread)}`}</div></td>
        <td class="mono">{velocity == null ? '--' : `${Number(velocity) >= 0 ? '+' : ''}${money(velocity)}`}</td>
        <td class="mono">{localTime(provider.last_update_utc)}</td>
      </tr>
    );
  });
}

function Comparisons({ comparisons }) {
  if (!comparisons.length) return <EmptyRow columns={5}>DUAL mode is not active. No matched executions yet.</EmptyRow>;
  return comparisons.map((comparison) => {
    const simulated = comparison.simulated || {};
    const real = comparison.real || {};
    const hasDelta = finite(comparison.pnl_delta_usd);
    const delta = hasDelta ? `${Number(comparison.pnl_delta_usd) >= 0 ? '+' : ''}${money(comparison.pnl_delta_usd)}` : '--';
    const fill = finite(real.fill_ratio) ? `${number(Number(real.fill_ratio) * 100, 1)}%` : '--';
    const latency = finite(real.entry_latency_ms) ? `${number(real.entry_latency_ms, 0)}ms` : '--';
    const simPnl = finite(simulated.net_pnl) ? `${Number(simulated.net_pnl) >= 0 ? '+' : ''}${money(simulated.net_pnl)}` : '--';
    const realPnl = finite(real.net_pnl) ? `${Number(real.net_pnl) >= 0 ? '+' : ''}${money(real.net_pnl)}` : '--';
    return (
      <tr key={comparison.comparison_id}>
        <td><strong style={{ color: comparison.side === 'LONG' ? 'var(--green)' : 'var(--orange)' }}>{comparison.side || '--'}</strong> <span class="mono" style={{ color: 'var(--purple)' }}>#{comparison.comparison_id}</span><div style={{ fontSize: '9px', color: 'var(--text-muted)' }}>{comparison.status || 'PENDING'}</div></td>
        <td class="mono">{finite(simulated.entry_price) ? money(simulated.entry_price, 1) : '--'} → {finite(simulated.exit_price) ? money(simulated.exit_price, 1) : '--'}<div style={{ fontSize: '9px', color: 'var(--text-muted)' }}>{simulated.status || '--'} · {simPnl}</div></td>
        <td class="mono">{finite(real.entry_price) ? money(real.entry_price, 1) : '--'} → {finite(real.exit_price) ? money(real.exit_price, 1) : '--'}<div style={{ fontSize: '9px', color: 'var(--text-muted)' }}>{real.status || '--'} · {realPnl}</div></td>
        <td class="mono">{fill}<div style={{ fontSize: '9px', color: 'var(--text-muted)' }}>entry {latency}</div></td>
        <td class={`mono ${hasDelta ? (Number(comparison.pnl_delta_usd) >= 0 ? 'win-tag' : 'loss-tag') : ''}`}>{delta}</td>
      </tr>
    );
  });
}

function Events({ events }) {
  if (!events.length) return <EmptyRow columns={5}>Monitoring breakout repricing cycles...</EmptyRow>;
  return events.map((event) => (
    <tr key={event.event_id || event.timestamp}>
      <td class="mono">{event.timestamp}</td>
      <td><strong style={{ color: 'var(--cyan)' }}>{event.direction}</strong></td>
      <td class="mono">{money(event.initial_lag_usd, 1)}</td>
      <td class="mono">{event.catchup_seconds}s</td>
      <td><span class={event.resolved ? 'win-tag' : 'loss-tag'}>{event.resolved ? 'RESOLVED' : 'TIMED_OUT'}</span></td>
    </tr>
  ));
}

function TableBlock({ title, endpoint, tall, children }) {
  return (
    <div class="table-block table-block-bottom">
      <div class="table-header"><span class="table-title">{title}</span><span class="table-sub mono">{endpoint}</span></div>
      <div class={`table-scroll ${tall ? 'table-scroll-tall' : ''}`}><table class="divided-table">{children}</table></div>
    </div>
  );
}

function Tables({ trades = [], providers = [], comparisons = [], events = [] }) {
  return (
    <div class="bottom-tables-grid">
      <div class="tables-col-left">
        <TableBlock title="Recent Sniper Trades (Zero Fees)" endpoint="GET /api/trades/history">
          <thead><tr><th>Time</th><th>Side</th><th>Size / Notional</th><th>Entry</th><th>Exit</th><th>Net PnL (RoM)</th><th>Hold</th><th>Reason</th></tr></thead>
          <tbody id="trades-table-body"><Trades trades={trades} /></tbody>
        </TableBlock>
        <TableBlock title="Provider Telemetry & Freshness" endpoint="GET /api/system/providers">
          <thead><tr><th>Provider / Role</th><th>Quality / Age</th><th>Updates</th><th>Mid / Spread</th><th>2s Move</th><th>Last Update</th></tr></thead>
          <tbody id="provider-insights-body"><Providers providers={providers} /></tbody>
        </TableBlock>
      </div>
      <div class="tables-col-right">
        <TableBlock title="DUAL Execution: Simulated vs Real" endpoint="GET /api/trades/comparisons">
          <thead><tr><th>Signal / Status</th><th>Simulated L2 Fill</th><th>Real Lighter Fill</th><th>Fill / Latency</th><th>Δ PnL</th></tr></thead>
          <tbody id="dual-comparisons-table-body"><Comparisons comparisons={comparisons} /></tbody>
        </TableBlock>
        <TableBlock title="Repricing Catch-Up Cycles" endpoint="GET /api/analytics/repricing-events" tall>
          <thead><tr><th>Time</th><th>Dir</th><th>Initial Lag</th><th>Duration</th><th>Status</th></tr></thead>
          <tbody id="repricing-table-body"><Events events={events} /></tbody>
        </TableBlock>
      </div>
    </div>
  );
}

export const DashboardTables = memo(Tables);
