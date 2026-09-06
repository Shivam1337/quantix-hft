import { memo } from 'preact/compat';
import { bytes, money, number, percent } from '../lib/format.js';

function ResourceCell({ label, value, detail }) {
  return (
    <div class="resource-cell">
      <span class="resource-label">{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

function Position({ position }) {
  if (!position) return <div class="active-pos-box mono" style={{ display: 'none' }} />;
  const pnl = Number(position.floating_pnl_usd) || 0;
  const price = position.entry_price ?? position.entry_px;
  const size = position.size_btc ?? position.size;
  const notional = position.notional_usd || Number(size || 0) * Number(price || 0);
  return (
    <div class="active-pos-box mono" style={{ display: 'block' }}>
      <div class="active-pos-header">● ACTIVE SNIPER POSITION (LIGHTER):</div>
      <div>
        {position.dual_execution && <><span style={{ color: 'var(--purple)' }}>DUAL CONTROL</span> · </>}
        <strong>{position.side || '--'}</strong> vs {position.leader_name || position.leader || 'Leader'} | <span style={{ color: 'var(--lighter-color)' }}>{size || '--'} BTC</span> ({money(notional, 0)} @ {position.leverage || 50}x) | Margin: <strong>{money(position.margin_allocated_usd)}</strong> | Entry: <strong>{money(price, 1)}</strong> | Target: <strong>{money(position.target_price ?? position.target_px, 1)}</strong> | Hold: <strong>{position.hold_seconds || 0}s</strong> | Floating PnL: <strong style={{ color: pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>{pnl >= 0 ? '+' : ''}{money(pnl)}</strong>
      </div>
    </div>
  );
}

function Cockpit({ state }) {
  const decision = state.trade_decision || {};
  const system = state.system || {};
  const resources = system.resources || {};
  const persistence = system.persistence || {};
  const stance = decision.stance || 'MONITORING';
  const consensus = state.consensus_status || 'CONSENSUS';
  const backend = persistence.backend === 'sqlite' ? 'SQLite (dev)' : 'PostgreSQL';
  const sampleDetail = resources.sample_interval_ms == null
    ? 'warming up CPU sampler'
    : `${resources.sample_interval_ms}ms sample`;
  const persistenceDetail = persistence.connected
    ? `${backend}: connected · ${persistence.records_written || 0} derived records saved`
    : `${backend}: ${persistence.last_error ? 'error — see API inspector' : 'connecting…'}`;
  return (
    <div class="left-divided-col">
      <div class="panel-block cockpit-panel">
        <div class="panel-header">
          <div class="panel-title"><span>⚡ DYNAMIC SNIPER COCKPIT</span><span class="zero-fee-pill">ZERO FEES</span></div>
          <div class="cockpit-badges">
            <span class={`stance-badge mono stance-${stance}`}>{stance}</span>
            <span class="leader-badge mono" style={{ fontSize: '10px', color: 'var(--bn-color)', fontWeight: 700 }}>👑 LEADER: {String(state.dynamic_leader || '--').toUpperCase()}</span>
            <span class={`consensus-badge mono consensus-${consensus}`} style={{ fontSize: '10px', color: 'var(--blue)' }}>{String(consensus).replace('_', ' ')}</span>
            <span class="agreement-badge mono" style={{ fontSize: '10px', color: 'var(--text-muted)' }}>{state.consensus_agreement || '5-WAY'}</span>
          </div>
        </div>
        <Position position={state.active_position} />
        <div class="rationale-box mono">{decision.rationale || 'Awaiting multi-exchange discovery updates...'}</div>
      </div>
      <div class="panel-block resources-panel">
        <div class="panel-header">
          <div class="panel-title">HOST &amp; SERVER RESOURCES</div>
          <div class="mono panel-telemetry-sub">{sampleDetail} · {resources.logical_cpu_count || '--'} logical CPUs | {persistenceDetail}</div>
        </div>
        <div class="resources-grid mono">
          <ResourceCell label="System CPU" value={percent(resources.system_cpu_percent)} detail="Whole machine" />
          <ResourceCell label="Server Process CPU" value={percent(resources.process_cpu_percent)} detail="Python server only" />
          <ResourceCell label="System RAM" value={percent(resources.system_memory_percent)} detail={`${bytes(resources.system_memory_used_bytes)} / ${bytes(resources.system_memory_total_bytes)}`} />
          <ResourceCell label="Server RAM" value={bytes(resources.process_memory_rss_bytes)} detail={`${percent(resources.process_memory_percent, 3)} of host RAM`} />
        </div>
      </div>
    </div>
  );
}

export const DashboardCockpit = memo(Cockpit);
