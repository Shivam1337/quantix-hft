import { fmt, fmtBytes, fmtDuration, fmtPercent, setHtml, setText } from './ui-utils.js';

function setClass(element, className) {
  if (element && element.className !== className) element.className = className;
}

function updateResources(resources) {
  if (!resources) return;
  setText('system-cpu', fmtPercent(resources.system_cpu_percent));
  setText('process-cpu', fmtPercent(resources.process_cpu_percent));
  setText('system-ram', fmtPercent(resources.system_memory_percent));
  setText('process-ram', fmtBytes(resources.process_memory_rss_bytes));
  setText('system-ram-detail', `${fmtBytes(resources.system_memory_used_bytes)} / ${fmtBytes(resources.system_memory_total_bytes)}`);
  setText('process-ram-detail', `${fmtPercent(resources.process_memory_percent, 3)} of host RAM`);
  const interval = resources.sample_interval_ms == null ? 'warming up CPU sampler' : `${resources.sample_interval_ms}ms sample`;
  setText('resource-sample-detail', `${interval} · ${resources.logical_cpu_count || '--'} logical CPUs`);
}

function updatePersistence(persistence) {
  if (!persistence) return;
  const backend = persistence.backend === 'sqlite' ? 'SQLite (dev)' : 'PostgreSQL';
  const detail = persistence.connected
    ? `${backend}: connected · ${persistence.records_written || 0} derived records saved`
    : `${backend}: ${persistence.last_error ? 'error — see API inspector' : 'connecting…'}`;
  setText('persistence-status', detail);
}

function updateMode(data) {
  const performance = data.trading_performance || {};
  const mode = performance.trading_mode || (performance.is_real_mode ? 'REAL' : 'SIMULATION');
  const enabled = (data.trading_enabled ?? performance.trading_enabled) !== false;
  const box = document.getElementById('sidebar-mode-box');
  const label = document.getElementById('sidebar-mode-label');
  const sub = document.getElementById('sidebar-mode-sub');
  if (!box || !label) return;
  box.classList.toggle('mode-paused', !enabled);
  box.classList.toggle('mode-real', enabled && mode === 'REAL');
  box.classList.toggle('mode-dual', enabled && mode === 'DUAL');
  setText(label, enabled ? mode : 'PAUSED');
  setText(sub, !enabled ? `${mode} · New entries blocked` : mode === 'DUAL' ? 'Live + matched simulation' : mode === 'REAL' ? 'Active On-Chain zkLighter' : 'Paper Trading (0 Risk)');
}

function updateDecision(data) {
  if (data.dynamic_leader) setText('leader-badge', `👑 LEADER: ${String(data.dynamic_leader).toUpperCase()}`);
  if (data.consensus_status) {
    const badge = document.getElementById('consensus-badge');
    setText(badge, String(data.consensus_status).replace('_', ' '));
    setClass(badge, `consensus-badge mono consensus-${data.consensus_status}`);
  }
  if (data.consensus_agreement) setText('agreement-badge', data.consensus_agreement);
  const decision = data.trade_decision;
  if (!decision) return;
  const badge = document.getElementById('stance-badge');
  setText(badge, decision.stance || 'MONITORING');
  setClass(badge, `stance-badge mono stance-${decision.stance || 'MONITORING'}`);
  setText('decision-rationale', decision.rationale || 'Engine monitoring dynamic leader…');
}

function updateActivePosition(position) {
  const box = document.getElementById('active-pos-box');
  if (!box) return;
  if (!position) {
    if (box.style.display !== 'none') box.style.display = 'none';
    return;
  }
  if (box.style.display !== 'block') box.style.display = 'block';
  const pnl = Number(position.floating_pnl_usd) || 0;
  const price = position.entry_price ?? position.entry_px;
  const size = position.size_btc ?? position.size;
  const notional = position.notional_usd || Number(size || 0) * Number(price || 0);
  const color = pnl >= 0 ? 'var(--green)' : 'var(--red)';
  const dual = position.dual_execution ? '<span style="color: var(--purple);">DUAL CONTROL</span> · ' : '';
  setHtml('pos-details', `${dual}<strong>${position.side || '--'}</strong> vs ${position.leader_name || position.leader || 'Leader'} | <span style="color: var(--lighter-color);">${size || '--'} BTC</span> ($${fmt(notional, 0)} @ ${position.leverage || 50}x) | Margin: <strong>$${fmt(position.margin_allocated_usd || 0, 2)}</strong> | Entry: <strong>$${fmt(price, 1)}</strong> | Target: <strong>$${fmt(position.target_price ?? position.target_px, 1)}</strong> | Hold: <strong>${position.hold_seconds || 0}s</strong> | Floating PnL: <strong style="color: ${color};">${pnl >= 0 ? '+' : ''}$${fmt(pnl, 2)}</strong>`);
}

function updatePerformance(performance, market) {
  if (!performance) return;
  const real = performance.is_real_mode === true;
  const ready = !real || performance.account_data_available === true;
  const money = (value, decimals = 2) => ready ? `$${fmt(value, decimals)}` : '--';
  setText('perf-equity-label', real ? 'Real Account Equity' : 'Account Equity');
  setText('perf-margin-label', real ? 'Real Margin Target' : `Margin @ ${performance.leverage ?? 50}x Leverage`);
  setText('perf-notional-label', real ? 'Real Target Notional' : 'Target Notional');
  setText('perf-net-pnl-label', real ? 'Net PnL (Confirmed Real)' : 'Net PnL (Lighter)');
  setText('perf-winrate-label', real ? 'Real Win Rate' : 'Win Rate');
  setText('perf-fees-label', real ? 'Fees Saved (Real)' : 'Fees Saved vs Poly');
  setText('perf-equity', money(performance.account_equity_usd));
  setText('perf-balance-sub', real
    ? (ready ? `Lighter collateral: $${fmt(performance.account_collateral_usd, 2)} · Free: $${fmt(performance.free_margin_usd, 2)}` : 'Awaiting verified Lighter account snapshot…')
    : `Base: $${fmt(performance.account_base_balance_usd, 0)} · Free: $${fmt(performance.free_margin_usd, 2)}`);
  const capped = real && Number(performance.configured_target_margin_usd) > Number(performance.target_margin_usd) + 0.0001;
  setText('perf-margin-alloc', `${money(performance.target_margin_usd)} (${capped ? `$${fmt(performance.configured_target_margin_usd, 2)} configured · free-margin cap` : `${fmt(performance.target_margin_fraction_pct ?? 0, 1)}% target · ${fmt(performance.margin_utilization_pct ?? 0, 1)}% used`})`);
  setText('perf-leverage-sub', real ? `${performance.leverage ?? 50}x configured · Exchange margin used: $${fmt(performance.margin_used_usd, 2)}` : `${performance.leverage ?? 50}x Lighter Leverage`);
  setText('perf-notional', money(performance.target_notional_usd, 0));
  const btc = ready && performance.target_notional_usd && market?.lighter?.mid_price
    ? (performance.target_notional_usd / market.lighter.mid_price).toFixed(4) : '--';
  setText('perf-size-sub', real ? `BTC target ~${btc} · Position: $${fmt(performance.account_position_notional_usd, 0)}` : `Dynamic ~${btc} BTC`);
  const net = Number(performance.net_pnl) || 0;
  const netElement = setText('perf-net-pnl', `${net >= 0 ? '+' : ''}$${fmt(net, 2)}`);
  if (netElement) netElement.style.color = net > 0 ? 'var(--green)' : net < 0 ? 'var(--red)' : 'var(--text)';
  const rom = Number(performance.return_on_margin_pct) || 0;
  setText('perf-rom-sub', real ? `Confirmed strategy PnL · RoM: ${rom >= 0 ? '+' : ''}${fmt(rom, 1)}%` : `RoM: ${rom >= 0 ? '+' : ''}${fmt(rom, 1)}% · 0% Fees`);
  setText('perf-winrate', `${performance.win_rate ?? 0}%`);
  setText('perf-trades-count', `${performance.total_trades ?? 0} ${real ? 'Confirmed Real' : ''} Trades (${performance.wins ?? 0}W / ${performance.losses ?? 0}L)`);
  setText('perf-saved-fees', `$${fmt(performance.fees_saved_vs_poly, 2)}`);
  setText('perf-saved-fees-sub', real ? `Est. vs ${fmt(performance.fees_saved_rate_pct, 2)}% round-trip alternative` : 'Avoided $64/BTC hurdle');
}

export function renderRealtime(data) {
  setText('update-clock', data.updated_at || '--:--:--');
  const system = data.system || {};
  setText('conn-status', `${system.status || 'DEGRADED'} · ${system.streaming_feeds ?? 0}/${system.total_feeds ?? 6} FEEDS · ${system.tick_rate_hz || 0} Hz`);
  const uptime = system.uptime_formatted || fmtDuration(system.uptime_seconds);
  setText('uptime-display', uptime);
  setText('sidebar-uptime-display', uptime);
  updateMode(data);
  updateDecision(data);
  updateActivePosition(data.active_position);
}

export function renderDetail(data) {
  updateResources(data.system?.resources);
  updatePersistence(data.system?.persistence);
  updatePerformance(data.trading_performance, data.market);
}
