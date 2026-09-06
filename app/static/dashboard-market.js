import { displayUtcTime, fmt, setText, topBookSize } from './ui-utils.js';

let providerSignature = '';
let tradeSignature = '';
let eventSignature = '';
let comparisonSignature = '';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[character]));
}

function setQuote(prefix, quote) {
  if (!quote) return;
  setText(`${prefix}-price`, `$${fmt(quote.mid_price, 1)}`);
  setText(`${prefix}-spread`, `Spread: $${fmt(quote.spread, 2)}`);
  setText(`${prefix}-bid`, `$${fmt(quote.best_bid, 1)}`);
  setText(`${prefix}-ask`, `$${fmt(quote.best_ask, 1)}`);
  setText(`${prefix}-status`, quote.status || 'WAITING');
}

function setLag(prefix, quote, label) {
  if (!quote) return;
  const lag = Number(quote.lag_vs_leader) || 0;
  const sign = lag >= 0 ? '+' : '';
  setText(`${prefix}-lag-sub`, `${label}: ${sign}$${fmt(lag, 2)} (${sign}${quote.lag_bps || 0} bps)`);
}

export function renderMarket(market) {
  if (!market) return;
  setQuote('bn', market.binance);
  setQuote('bybit', market.bybit);
  setQuote('okx', market.okx);
  setQuote('hl', market.hyperliquid);
  setQuote('poly', market.polymarket);
  setQuote('lighter', market.lighter);
  if (market.polymarket) {
    setLag('poly', market.polymarket, 'Lag vs Leader');
    setText('poly-spread', `$${fmt(market.polymarket.spread, 2)}`);
  }
  if (market.lighter) {
    setText('lighter-bid-size', topBookSize(market.lighter.top_bid_size));
    setText('lighter-ask-size', topBookSize(market.lighter.top_ask_size));
    setLag('lighter', market.lighter, 'Lag vs Leader');
    setText('lighter-spread', `$${fmt(market.lighter.spread, 2)}`);
  }
}

function appendCell(row, text, className) {
  const cell = document.createElement('td');
  if (className) cell.className = className;
  cell.textContent = text;
  row.appendChild(cell);
  return cell;
}

export function renderProviderInsights(insights) {
  const body = document.getElementById('provider-insights-body');
  const providers = Array.isArray(insights?.providers) ? insights.providers : [];
  const signature = providers.map((provider) => `${provider.id}:${provider.updates}:${provider.data_quality}:${provider.age_ms}`).join('|');
  if (!body || !providers.length || signature === providerSignature) return;
  providerSignature = signature;
  const fragment = document.createDocumentFragment();
  providers.forEach((provider) => {
    const row = document.createElement('tr');
    const name = appendCell(row, provider.name || provider.id || 'Unknown provider');
    const role = document.createElement('div');
    role.className = 'provider-role';
    role.textContent = provider.role || '--';
    name.appendChild(role);
    const quality = String(provider.data_quality || 'WAITING').toLowerCase();
    const age = provider.age_ms == null ? '--' : `${fmt(provider.age_ms, 1)} ms`;
    const qualityCell = appendCell(row, `${provider.data_quality || 'WAITING'} · ${age}`, `provider-quality provider-${quality}`);
    qualityCell.title = provider.connection_status || '';
    appendCell(row, Number(provider.updates || 0).toLocaleString('en-US'), 'mono');
    const price = provider.mid_price == null ? '--' : `$${fmt(provider.mid_price, 1)}`;
    const priceCell = appendCell(row, price, 'mono');
    const spread = document.createElement('div');
    spread.className = 'provider-role mono';
    spread.textContent = provider.spread == null ? 'spread --' : `spread $${fmt(provider.spread, 2)}`;
    priceCell.appendChild(spread);
    const velocity = provider.velocity_usd_2s;
    appendCell(row, velocity == null ? '--' : `${Number(velocity) >= 0 ? '+' : ''}$${fmt(velocity, 2)}`, 'mono');
    appendCell(row, displayUtcTime(provider.last_update_utc), 'mono');
    fragment.appendChild(row);
  });
  body.replaceChildren(fragment);
}

function tradeRow(trade) {
  const margin = Number(trade.margin_allocated_usd) || 50;
  const pnl = Number(trade.net_pnl) || 0;
  const rom = ((pnl / margin) * 100).toFixed(1);
  const size = trade.size_btc ?? trade.size ?? '--';
  const notional = trade.notional_usd
    ? `$${fmt(trade.notional_usd, 0)}` : `$${fmt(Number(size) * Number(trade.entry_px || 0), 0)}`;
  return `<tr><td class="mono">${escapeHtml(trade.time)}</td><td><strong style="color: ${trade.side === 'LONG' ? 'var(--green)' : 'var(--orange)'};">${escapeHtml(trade.side)}</strong> <span style="font-size: 9px; color: var(--text-muted);">${escapeHtml(trade.leader)}</span></td><td><span class="mono">${escapeHtml(size)} BTC</span> <span style="font-size: 9px; color: var(--lighter-color);">${notional} @ ${escapeHtml(trade.leverage || 50)}x</span></td><td class="mono">$${fmt(trade.entry_px, 1)}</td><td class="mono">$${fmt(trade.exit_px, 1)}</td><td class="mono ${trade.is_win ? 'win-tag' : 'loss-tag'}">${pnl >= 0 ? '+' : ''}$${fmt(pnl, 2)} <span style="font-size: 9px; color: var(--text-muted);">(${rom >= 0 ? '+' : ''}${rom}%)</span></td><td class="mono">${escapeHtml(trade.hold_sec)}s</td><td style="color: var(--text-muted); font-size: 10px;">${escapeHtml(trade.reason)}</td></tr>`;
}

export function renderTrades(trades) {
  const body = document.getElementById('trades-table-body');
  const records = Array.isArray(trades) ? trades : [];
  const signature = records.map((trade) => `${trade.id}:${trade.time}:${trade.net_pnl}`).join('|');
  if (!body || signature === tradeSignature) return;
  tradeSignature = signature;
  body.innerHTML = records.length
    ? records.map(tradeRow).join('')
    : '<tr><td colspan="8" style="color: var(--text-muted); text-align: center;">No closed paper trades recorded yet.</td></tr>';
}

function hasNumber(value) {
  return Number.isFinite(Number(value));
}

function comparisonPrice(value) {
  return hasNumber(value) ? `$${fmt(value, 1)}` : '--';
}

function comparisonPnl(value) {
  if (!hasNumber(value)) return '--';
  const pnl = Number(value);
  return `${pnl >= 0 ? '+' : ''}$${fmt(pnl, 2)}`;
}

function comparisonRow(comparison) {
  const simulated = comparison.simulated || {};
  const real = comparison.real || {};
  const side = escapeHtml(comparison.side || '--');
  const status = escapeHtml(comparison.status || 'PENDING');
  const simPnl = comparisonPnl(simulated.net_pnl);
  const realPnl = comparisonPnl(real.net_pnl);
  const delta = comparisonPnl(comparison.pnl_delta_usd);
  const deltaClass = hasNumber(comparison.pnl_delta_usd) && Number(comparison.pnl_delta_usd) >= 0 ? 'win-tag' : 'loss-tag';
  const fillRatio = hasNumber(real.fill_ratio) ? `${fmt(Number(real.fill_ratio) * 100, 1)}%` : '--';
  const entryLatency = hasNumber(real.entry_latency_ms) ? `${fmt(real.entry_latency_ms, 0)}ms` : '--';
  return `<tr><td><strong style="color: ${comparison.side === 'LONG' ? 'var(--green)' : 'var(--orange)'};">${side}</strong> <span class="mono" style="color: var(--purple);">#${escapeHtml(comparison.comparison_id)}</span><div style="font-size: 9px; color: var(--text-muted);">${status}</div></td><td class="mono">${comparisonPrice(simulated.entry_price)} → ${comparisonPrice(simulated.exit_price)}<div style="font-size: 9px; color: var(--text-muted);">${escapeHtml(simulated.status || '--')} · ${simPnl}</div></td><td class="mono">${comparisonPrice(real.entry_price)} → ${comparisonPrice(real.exit_price)}<div style="font-size: 9px; color: var(--text-muted);">${escapeHtml(real.status || '--')} · ${realPnl}</div></td><td class="mono">${fillRatio}<div style="font-size: 9px; color: var(--text-muted);">entry ${entryLatency}</div></td><td class="mono ${hasNumber(comparison.pnl_delta_usd) ? deltaClass : ''}">${delta}</td></tr>`;
}

export function renderExecutionComparisons(comparisons) {
  const body = document.getElementById('dual-comparisons-table-body');
  const records = Array.isArray(comparisons) ? comparisons : [];
  const signature = records.map((comparison) => `${comparison.comparison_id}:${comparison.status}:${comparison.updated_at}`).join('|');
  if (!body || signature === comparisonSignature) return;
  comparisonSignature = signature;
  body.innerHTML = records.length
    ? records.map(comparisonRow).join('')
    : '<tr><td colspan="5" style="color: var(--text-muted); text-align: center;">DUAL mode is not active. No matched executions yet.</td></tr>';
}

function eventRow(event) {
  return `<tr><td class="mono">${escapeHtml(event.timestamp)}</td><td><strong style="color: var(--cyan);">${escapeHtml(event.direction)}</strong></td><td class="mono">$${fmt(event.initial_lag_usd, 1)}</td><td class="mono">${escapeHtml(event.catchup_seconds)}s</td><td><span class="${event.resolved ? 'win-tag' : 'loss-tag'}">${event.resolved ? 'RESOLVED' : 'TIMED_OUT'}</span></td></tr>`;
}

export function renderRepricingEvents(events) {
  const body = document.getElementById('repricing-table-body');
  const records = Array.isArray(events) ? events : [];
  const signature = records.map((event) => `${event.event_id || event.timestamp}:${event.resolved}`).join('|');
  if (!body || signature === eventSignature) return;
  eventSignature = signature;
  body.innerHTML = records.map(eventRow).join('');
}

export function resetActivityTables() {
  tradeSignature = '';
  eventSignature = '';
  comparisonSignature = '';
  const trades = document.getElementById('trades-table-body');
  const events = document.getElementById('repricing-table-body');
  const comparisons = document.getElementById('dual-comparisons-table-body');
  if (trades) trades.innerHTML = '<tr><td colspan="8" style="color: var(--text-muted); text-align: center;">Awaiting trades...</td></tr>';
  if (events) events.innerHTML = '<tr><td colspan="5" style="color: var(--text-muted); text-align: center;">Monitoring breakout repricing cycles...</td></tr>';
  if (comparisons) comparisons.innerHTML = '<tr><td colspan="5" style="color: var(--text-muted); text-align: center;">Awaiting matched DUAL executions...</td></tr>';
}
