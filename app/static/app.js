// Frontend Dashboard Controller & SSE Consumer

function fmt(n, d = 2) {
  if (n === null || n === undefined || isNaN(n)) return '--';
  return Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

function topBookSize(levels) {
  const value = Array.isArray(levels) && Array.isArray(levels[0]) ? Number(levels[0][1]) : NaN;
  return Number.isFinite(value) && value > 0 ? `${fmt(value, 5)} BTC` : '-- BTC';
}

function fmtBytes(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let amount = Number(value);
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toLocaleString('en-US', { maximumFractionDigits: amount >= 10 ? 1 : 2 })} ${units[unit]}`;
}

function fmtPercent(value, digits = 1) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '--';
  return `${Number(value).toFixed(digits)}%`;
}

function currencyTick(value) {
  return `$${Number(value).toLocaleString('en-US', { maximumFractionDigits: Math.abs(value) >= 1000 ? 0 : 2 })}`;
}

function fmtDuration(totalSeconds) {
  if (totalSeconds === null || totalSeconds === undefined || !Number.isFinite(Number(totalSeconds))) return '00:00:00';
  const sec = Math.max(0, Math.floor(Number(totalSeconds)));
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// Local canvas charts avoid a network-dependent Chart.js CDN and gracefully render missing feeds as gaps.
const priceChart = typeof CanvasLineChart === 'function'
  ? new CanvasLineChart(document.getElementById('priceChart'), {
    datasets: [
      { id: 'binance', label: 'Binance', color: '#f3ba2f' },
      { id: 'bybit', label: 'Bybit', color: '#f7a600' },
      { id: 'okx', label: 'OKX', color: '#38bdf8' },
      { id: 'hyperliquid', label: 'Hyperliquid', color: '#10e598' },
      { id: 'polymarket', label: 'Polymarket', color: '#00d2ff' },
      { id: 'lighter', label: 'Lighter', color: '#f59e0b', lineWidth: 2.5 },
    ],
    yFormatter: currencyTick,
    emptyMessage: 'Waiting for live provider price samples…',
  })
  : null;

const lagChart = typeof CanvasLineChart === 'function'
  ? new CanvasLineChart(document.getElementById('lagChart'), {
    datasets: [
      { id: 'lighterLag', label: 'Lighter vs leader', color: '#f59e0b', lineWidth: 2.5 },
    ],
    thresholds: [
      { value: 6.0, label: 'SHORT SNIPE (+$6)', color: '#ff4d6d' },
      { value: 0.0, label: 'PARITY ($0)', color: '#64748b' },
      { value: -6.0, label: 'LONG SNIPE (-$6)', color: '#10e598' },
    ],
    yFormatter: (value) => `${value >= 0 ? '+' : ''}$${value.toFixed(2)}`,
    emptyMessage: 'Waiting for Lighter and leader samples…',
  })
  : null;

function updateLagChartThresholds(minLag) {
  if (!lagChart || typeof lagChart.setThresholds !== 'function') return;
  const lagVal = Math.abs(parseFloat(minLag)) || 6.0;
  lagChart.setThresholds([
    { value: lagVal, label: `SHORT SNIPE (+$${lagVal.toFixed(1)})`, color: '#ff4d6d' },
    { value: 0.0, label: 'PARITY ($0)', color: '#64748b' },
    { value: -lagVal, label: `LONG SNIPE (-$${lagVal.toFixed(1)})`, color: '#10e598' },
  ]);
  const titleEl = document.getElementById('lag-chart-title');
  if (titleEl) {
    titleEl.textContent = `LIGHTER.XYZ LAG VS DYNAMIC LEADER & SNIPE THRESHOLDS (±$${lagVal.toFixed(2)})`;
  }
}

// Real-time EventSource Stream
const evtSource = new EventSource('/api/system/stream');

evtSource.onmessage = function(event) {
  try {
    const data = JSON.parse(event.data);
    updateDashboard(data);
  } catch (err) {
    console.error('Failed to parse SSE tick:', err);
  }
};

evtSource.onerror = function() {
  document.getElementById('conn-status').innerText = 'RECONNECTING...';
};

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
  return element;
}

function displayUtcTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
}

function updateResourcePanel(resources) {
  if (!resources) return;
  setText('system-cpu', fmtPercent(resources.system_cpu_percent));
  setText('process-cpu', fmtPercent(resources.process_cpu_percent));
  setText('system-ram', fmtPercent(resources.system_memory_percent));
  setText('process-ram', fmtBytes(resources.process_memory_rss_bytes));
  setText(
    'system-ram-detail',
    `${fmtBytes(resources.system_memory_used_bytes)} / ${fmtBytes(resources.system_memory_total_bytes)}`
  );
  setText(
    'process-ram-detail',
    `${fmtPercent(resources.process_memory_percent, 3)} of host RAM`
  );
  const interval = resources.sample_interval_ms === null || resources.sample_interval_ms === undefined
    ? 'warming up CPU sampler'
    : `${resources.sample_interval_ms}ms sample`;
  setText('resource-sample-detail', `${interval} · ${resources.logical_cpu_count || '--'} logical CPUs`);
}

function updatePersistenceStatus(persistence) {
  if (!persistence) return;
  const backend = persistence.backend === 'sqlite' ? 'SQLite (dev)' : 'PostgreSQL';
  if (persistence.connected) {
    setText(
      'persistence-status',
      `${backend}: connected · ${persistence.records_written || 0} derived records saved`
    );
    return;
  }
  const detail = persistence.last_error ? 'error — see API inspector' : 'connecting…';
  setText('persistence-status', `${backend}: ${detail}`);
}


function appendCell(row, text, className) {
  const cell = document.createElement('td');
  if (className) cell.className = className;
  cell.textContent = text;
  row.appendChild(cell);
  return cell;
}

function updateProviderInsights(insights) {
  const body = document.getElementById('provider-insights-body');
  const providers = insights && Array.isArray(insights.providers) ? insights.providers : [];
  if (!body || !providers.length) return;

  const fragment = document.createDocumentFragment();
  providers.forEach((provider) => {
    const row = document.createElement('tr');
    const nameCell = appendCell(row, provider.name || provider.id || 'Unknown provider');
    const role = document.createElement('div');
    role.className = 'provider-role';
    role.textContent = provider.role || '--';
    nameCell.appendChild(role);

    const quality = String(provider.data_quality || 'WAITING').toLowerCase();
    const age = provider.age_ms === null || provider.age_ms === undefined ? '--' : `${fmt(provider.age_ms, 1)} ms`;
    const qualityCell = appendCell(row, `${provider.data_quality || 'WAITING'} · ${age}`, `provider-quality provider-${quality}`);
    qualityCell.title = provider.connection_status || '';
    appendCell(row, Number(provider.updates || 0).toLocaleString('en-US'), 'mono');

    const price = provider.mid_price === null || provider.mid_price === undefined
      ? '--'
      : `$${fmt(provider.mid_price, 1)}`;
    const spread = provider.spread === null || provider.spread === undefined
      ? 'spread --'
      : `spread $${fmt(provider.spread, 2)}`;
    const priceCell = appendCell(row, price, 'mono');
    const spreadLine = document.createElement('div');
    spreadLine.className = 'provider-role mono';
    spreadLine.textContent = spread;
    priceCell.appendChild(spreadLine);

    const velocity = provider.velocity_usd_2s;
    const movement = velocity === null || velocity === undefined
      ? '--'
      : `${Number(velocity) >= 0 ? '+' : ''}$${fmt(velocity, 2)}`;
    appendCell(row, movement, 'mono');
    appendCell(row, displayUtcTime(provider.last_update_utc), 'mono');
    fragment.appendChild(row);
  });
  body.replaceChildren(fragment);
}

function updateCharts(chart, providerInsights) {
  const timestamps = chart && Array.isArray(chart.timestamps) ? chart.timestamps : [];
  if (priceChart) {
    priceChart.setData(timestamps, {
      binance: chart?.binance_series,
      bybit: chart?.bybit_series,
      okx: chart?.okx_series,
      hyperliquid: chart?.hl_series,
      polymarket: chart?.poly_series,
      lighter: chart?.lighter_series,
    });
  }
  if (lagChart) lagChart.setData(timestamps, { lighterLag: chart?.lighter_lag_series });

  const sampleCount = chart?.sample_count ?? timestamps.length;
  const interval = chart?.sample_interval_ms ? ` · ${chart.sample_interval_ms}ms cadence` : '';
  const providers = providerInsights && Array.isArray(providerInsights.providers)
    ? providerInsights.providers
    : [];
  const freshProviders = providers.filter((provider) => provider.fresh).length;
  const status = sampleCount
    ? `${sampleCount} persisted/live samples${interval}`
    : `No sample yet · ${freshProviders}/${providers.length || 6} providers fresh`;
  setText('price-chart-status', priceChart ? status : 'Local chart renderer unavailable');
  setText('lag-chart-status', lagChart ? status : 'Local chart renderer unavailable');
}

function updateDashboard(data) {
  // 1. Header & System
  document.getElementById('update-clock').innerText = data.updated_at || '--:--:--';
  if (data.system) {
    const streaming = data.system.streaming_feeds ?? 0;
    const total = data.system.total_feeds ?? 6;
    document.getElementById('conn-status').innerText = `${data.system.status || 'DEGRADED'} · ${streaming}/${total} FEEDS · ${data.system.tick_rate_hz || 0} Hz`;
    const upt = data.system.uptime_formatted || fmtDuration(data.system.uptime_seconds);
    document.getElementById('uptime-display').innerText = upt;
    const sideUptime = document.getElementById('sidebar-uptime-display');
    if (sideUptime) sideUptime.innerText = upt;

    updateResourcePanel(data.system.resources);
    updatePersistenceStatus(data.system.persistence);
  }

  // Sidebar Mode Indicator
  const mode = data.performance?.trading_mode || (data.performance?.is_real_mode ? 'REAL' : 'SIMULATION');
  const sideBox = document.getElementById('sidebar-mode-box');
  const sideLabel = document.getElementById('sidebar-mode-label');
  const sideSub = document.getElementById('sidebar-mode-sub');
  if (sideBox && sideLabel) {
    sideLabel.textContent = mode;
    if (mode === 'REAL') {
      sideBox.classList.add('mode-real');
      if (sideSub) sideSub.textContent = 'Active On-Chain zkLighter';
    } else {
      sideBox.classList.remove('mode-real');
      if (sideSub) sideSub.textContent = 'Paper Trading (0 Risk)';
    }
  }

  // 2. Dynamic Leader & Consensus Badges
  if (data.dynamic_leader) {
    const lBadge = document.getElementById('leader-badge');
    if (lBadge) lBadge.innerText = `👑 LEADER: ${data.dynamic_leader.toUpperCase()}`;
  }
  if (data.consensus_status) {
    const cBadge = document.getElementById('consensus-badge');
    if (cBadge) {
      cBadge.innerText = data.consensus_status.replace('_', ' ');
      cBadge.className = `consensus-badge mono consensus-${data.consensus_status}`;
    }
  }
  if (data.consensus_agreement) {
    const aBadge = document.getElementById('agreement-badge');
    if (aBadge) aBadge.innerText = data.consensus_agreement;
  }

  // 3. Trade Decisions & Rationale
  if (data.trade_decision) {
    const dec = data.trade_decision;
    const badge = document.getElementById('stance-badge');
    badge.innerText = dec.stance || 'MONITORING';
    badge.className = `stance-badge mono stance-${dec.stance || 'MONITORING'}`;
    document.getElementById('decision-rationale').innerText = dec.rationale || 'Engine monitoring dynamic leader...';
  }

  // 4. Active Position
  const posBox = document.getElementById('active-pos-box');
  if (data.active_position) {
    const pos = data.active_position;
    posBox.style.display = 'block';
    const flColor = pos.floating_pnl_usd >= 0 ? 'var(--green)' : 'var(--red)';
    const notional = pos.notional_usd ? `$${fmt(pos.notional_usd, 0)}` : `$${fmt((pos.size_btc ?? pos.size) * (pos.entry_price ?? pos.entry_px), 0)}`;
    document.getElementById('pos-details').innerHTML = `
      <strong>${pos.side}</strong> vs ${pos.leader_name || 'Leader'} | <span style="color: var(--lighter-color);">${(pos.size_btc ?? pos.size)} BTC</span> (${notional} @ ${pos.leverage || 50}x) |
      Margin: <strong>$${fmt(pos.margin_allocated_usd || 50, 2)}</strong> |
      Entry: <strong>$${fmt(pos.entry_price ?? pos.entry_px, 1)}</strong> |
      Target: <strong>$${fmt(pos.target_price ?? pos.target_px, 1)}</strong> |
      Hold: <strong>${pos.hold_seconds}s</strong> |
      Floating PnL: <strong style="color: ${flColor};">${pos.floating_pnl_usd >= 0 ? '+' : ''}$${fmt(pos.floating_pnl_usd, 2)}</strong>
    `;
  } else {
    posBox.style.display = 'none';
  }

  // 5. Performance & Dynamic Capital Metrics
  if (data.trading_performance) {
    const perf = data.trading_performance;
    setText('perf-equity', `$${fmt(perf.account_equity_usd ?? 100, 2)}`);
    setText('perf-balance-sub', `Base: $${fmt(perf.account_base_balance_usd ?? 100, 0)} · Free: $${fmt(perf.free_margin_usd ?? 100, 2)}`);
    setText('perf-margin-alloc', `$${fmt(perf.target_margin_usd ?? 50, 2)} (50%)`);
    setText('perf-leverage-sub', `${perf.leverage ?? 50}x Lighter Leverage`);
    setText('perf-notional', `$${fmt(perf.target_notional_usd ?? 2500, 0)}`);
    const dynamicBtc = (perf.target_notional_usd && data.market?.lighter?.mid_price)
      ? (perf.target_notional_usd / data.market.lighter.mid_price).toFixed(4)
      : '0.0280';
    setText('perf-size-sub', `Dynamic ~${dynamicBtc} BTC`);

    const netPnlEl = document.getElementById('perf-net-pnl');
    if (netPnlEl) {
      netPnlEl.innerText = `${perf.net_pnl >= 0 ? '+' : ''}$${fmt(perf.net_pnl, 2)}`;
      netPnlEl.style.color = perf.net_pnl > 0 ? 'var(--green)' : (perf.net_pnl < 0 ? 'var(--red)' : 'var(--text)');
    }
    const rom = perf.return_on_margin_pct ?? 0;
    setText('perf-rom-sub', `RoM: ${rom >= 0 ? '+' : ''}${fmt(rom, 1)}% · 0% Fees`);

    setText('perf-winrate', `${perf.win_rate}%`);
    setText('perf-trades-count', `${perf.total_trades} Trades (${perf.wins}W / ${perf.losses}L)`);
    setText('perf-saved-fees', `$${fmt(perf.fees_saved_vs_poly, 2)}`);
  }

  // 6. 6-Exchange Cards (5 Signals + 1 Execution Target)
  const mkt = data.market || {};

  // A. Binance
  if (mkt.binance) {
    const b = mkt.binance;
    document.getElementById('bn-price').innerText = `$${fmt(b.mid_price, 1)}`;
    document.getElementById('bn-spread').innerText = `Spread: $${fmt(b.spread, 2)}`;
    document.getElementById('bn-bid').innerText = `$${fmt(b.best_bid, 1)}`;
    document.getElementById('bn-ask').innerText = `$${fmt(b.best_ask, 1)}`;
    document.getElementById('bn-status').innerText = b.status || 'WS STREAMING';
  }

  // B. Bybit
  if (mkt.bybit) {
    const by = mkt.bybit;
    document.getElementById('bybit-price').innerText = `$${fmt(by.mid_price, 1)}`;
    document.getElementById('bybit-spread').innerText = `Spread: $${fmt(by.spread, 2)}`;
    document.getElementById('bybit-bid').innerText = `$${fmt(by.best_bid, 1)}`;
    document.getElementById('bybit-ask').innerText = `$${fmt(by.best_ask, 1)}`;
    document.getElementById('bybit-status').innerText = by.status || 'WS STREAMING';
  }

  // C. OKX
  if (mkt.okx) {
    const o = mkt.okx;
    document.getElementById('okx-price').innerText = `$${fmt(o.mid_price, 1)}`;
    document.getElementById('okx-spread').innerText = `Spread: $${fmt(o.spread, 2)}`;
    document.getElementById('okx-bid').innerText = `$${fmt(o.best_bid, 1)}`;
    document.getElementById('okx-ask').innerText = `$${fmt(o.best_ask, 1)}`;
    document.getElementById('okx-status').innerText = o.status || 'WS STREAMING';
  }

  // D. Hyperliquid
  if (mkt.hyperliquid) {
    const h = mkt.hyperliquid;
    document.getElementById('hl-price').innerText = `$${fmt(h.mid_price, 1)}`;
    document.getElementById('hl-spread').innerText = `Spread: $${fmt(h.spread, 2)}`;
    document.getElementById('hl-bid').innerText = `$${fmt(h.best_bid, 1)}`;
    document.getElementById('hl-ask').innerText = `$${fmt(h.best_ask, 1)}`;
    document.getElementById('hl-status').innerText = h.status || 'WS STREAMING';
  }

  // E. Polymarket
  if (mkt.polymarket) {
    const p = mkt.polymarket;
    document.getElementById('poly-price').innerText = `$${fmt(p.mid_price, 1)}`;
    document.getElementById('poly-bid').innerText = `$${fmt(p.best_bid, 1)}`;
    document.getElementById('poly-ask').innerText = `$${fmt(p.best_ask, 1)}`;
    const pSign = (p.lag_vs_leader || 0) >= 0 ? '+' : '';
    document.getElementById('poly-lag-sub').innerText = `Lag vs Leader: ${pSign}$${fmt(p.lag_vs_leader || 0, 2)} (${pSign}${p.lag_bps || 0} bps)`;
    document.getElementById('poly-spread').innerText = `$${fmt(p.spread, 2)}`;
    document.getElementById('poly-status').innerText = p.status || 'WS STREAMING';
  }

  // F. Lighter.xyz (0% Fee Execution Target)
  if (mkt.lighter) {
    const l = mkt.lighter;
    document.getElementById('lighter-price').innerText = `$${fmt(l.mid_price, 1)}`;
    document.getElementById('lighter-bid').innerText = `$${fmt(l.best_bid, 1)}`;
    document.getElementById('lighter-ask').innerText = `$${fmt(l.best_ask, 1)}`;
    setText('lighter-bid-size', topBookSize(l.bids));
    setText('lighter-ask-size', topBookSize(l.asks));
    const lSign = (l.lag_vs_leader || 0) >= 0 ? '+' : '';
    document.getElementById('lighter-lag-sub').innerText = `Lag vs Leader: ${lSign}$${fmt(l.lag_vs_leader || 0, 2)} (${lSign}${l.lag_bps || 0} bps)`;
    document.getElementById('lighter-spread').innerText = `$${fmt(l.spread, 2)}`;
    document.getElementById('lighter-status').innerText = l.status || 'CONNECTING...';
  }

  // 7. Provider health and local, gap-aware canvas charts.
  updateProviderInsights(data.provider_insights);
  updateCharts(data.chart, data.provider_insights);


  // 8. Recent Trades Table
  if (Array.isArray(data.recent_trades)) {
    const tbody = document.getElementById('trades-table-body');
    if (tbody) {
      if (data.recent_trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="color: var(--text-muted); text-align: center;">No closed paper trades recorded yet.</td></tr>';
      } else {
        tbody.innerHTML = data.recent_trades.map(t => {
          const margin = t.margin_allocated_usd || 50.0;
          const rom = ((t.net_pnl / margin) * 100).toFixed(1);
          const notional = t.notional_usd ? `$${fmt(t.notional_usd, 0)}` : `$${fmt((t.size_btc || 0.028) * t.entry_px, 0)}`;
          return `
          <tr>
            <td class="mono">${t.time}</td>
            <td><strong style="color: ${t.side === 'LONG' ? 'var(--green)' : 'var(--orange)'};">${t.side}</strong> <span style="font-size: 9px; color: var(--text-muted);">${t.leader || ''}</span></td>
            <td><span class="mono">${t.size_btc || t.size} BTC</span> <span style="font-size: 9px; color: var(--lighter-color);">${notional} @ ${t.leverage || 50}x</span></td>
            <td class="mono">$${fmt(t.entry_px, 1)}</td>
            <td class="mono">$${fmt(t.exit_px, 1)}</td>
            <td class="mono ${t.is_win ? 'win-tag' : 'loss-tag'}">${t.net_pnl >= 0 ? '+' : ''}$${fmt(t.net_pnl, 2)} <span style="font-size: 9px; color: var(--text-muted);">(${rom >= 0 ? '+' : ''}${rom}%)</span></td>
            <td class="mono">${t.hold_sec}s</td>
            <td style="color: var(--text-muted); font-size: 10px;">${t.reason}</td>
          </tr>
          `;
        }).join('');
      }
    }
  }

  // 9. Repricing Catch-up Events Table
  if (data.recent_repricing_events) {
    const rbody = document.getElementById('repricing-table-body');
    rbody.innerHTML = data.recent_repricing_events.map(r => `
      <tr>
        <td class="mono">${r.timestamp}</td>
        <td><strong style="color: var(--cyan);">${r.direction}</strong></td>
        <td class="mono">$${fmt(r.initial_lag_usd, 1)}</td>
        <td class="mono">${r.catchup_seconds}s</td>
        <td><span class="${r.resolved ? 'win-tag' : 'loss-tag'}">${r.resolved ? 'RESOLVED' : 'TIMED_OUT'}</span></td>
      </tr>
    `).join('');
  }
}

// =============================================================================
// API Inspector & Query Studio
// =============================================================================
async function queryApi(endpoint, title) {
  const container = document.getElementById('json-preview-container');
  const titleEl = document.getElementById('json-preview-title');
  const contentEl = document.getElementById('json-preview-content');
  const badgeEl = document.getElementById('api-status-badge');
  const latEl = document.getElementById('api-latency-badge');

  if (titleEl) titleEl.innerText = `ENDPOINT: ${title || endpoint} (${endpoint})`;
  if (contentEl) contentEl.innerText = 'Fetching live data...';
  if (badgeEl) {
    badgeEl.textContent = 'FETCHING...';
    badgeEl.className = 'badge badge-info mono';
  }
  if (container) container.style.display = 'block';

  const t0 = performance.now();
  try {
    const res = await fetch(endpoint);
    const elapsed = Math.round(performance.now() - t0);
    const json = await res.json();
    if (contentEl) contentEl.innerText = JSON.stringify(json, null, 2);
    if (titleEl) titleEl.innerText = `ENDPOINT: ${title || endpoint} (${endpoint})`;
    if (badgeEl) {
      badgeEl.textContent = `${res.status} ${res.statusText || 'OK'}`;
      badgeEl.className = res.ok ? 'badge badge-success mono' : 'badge badge-danger mono';
    }
    if (latEl) latEl.textContent = `${elapsed} ms`;
  } catch (err) {
    const elapsed = Math.round(performance.now() - t0);
    if (contentEl) contentEl.innerText = `Error querying ${endpoint}: ${err}`;
    if (badgeEl) {
      badgeEl.textContent = 'ERROR';
      badgeEl.className = 'badge badge-danger mono';
    }
    if (latEl) latEl.textContent = `${elapsed} ms`;
  }
}

function selectAndQueryApi(endpoint, title) {
  const inputEl = document.getElementById('api-custom-endpoint');
  if (inputEl) inputEl.value = endpoint;
  queryApi(endpoint, title);
}

function sendCustomApiQuery() {
  const inputEl = document.getElementById('api-custom-endpoint');
  const endpoint = inputEl ? inputEl.value.trim() : '/api/market/prices';
  queryApi(endpoint, 'Custom Request');
}

function clearApiResponse() {
  const contentEl = document.getElementById('json-preview-content');
  const titleEl = document.getElementById('json-preview-title');
  const badgeEl = document.getElementById('api-status-badge');
  const latEl = document.getElementById('api-latency-badge');
  if (contentEl) contentEl.innerText = 'Response cleared. Select an endpoint or click "Send Request".';
  if (titleEl) titleEl.innerText = 'QUERY RESULT';
  if (badgeEl) {
    badgeEl.textContent = 'IDLE';
    badgeEl.className = 'badge badge-info mono';
  }
  if (latEl) latEl.textContent = '-- ms';
}

function copyApiResponse(btn) {
  const contentEl = document.getElementById('json-preview-content');
  if (!contentEl) return;
  copyText(contentEl.innerText, btn);
}

function initApiInspector() {
  const contentEl = document.getElementById('json-preview-content');
  if (contentEl && (contentEl.innerText.startsWith('Click any endpoint') || !contentEl.innerText.trim())) {
    selectAndQueryApi('/api/market/prices', 'Rapid 6-Way Prices');
  }
}

function closePreview() {
  document.getElementById('json-preview-container').style.display = 'none';
}

// Total Database Disk Footprint (Manual refresh + page load only; NO polling)
async function fetchDbSize() {
  const valEl = document.getElementById('db-size-val');
  const backendEl = document.getElementById('db-size-backend');
  const timeEl = document.getElementById('db-checked-at');
  if (!valEl) return;

  try {
    const res = await fetch('/api/system/database-size', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    valEl.textContent = data.formatted || (data.size_mb ? `${data.size_mb.toFixed(2)} MB` : '-- MB');
    if (backendEl && data.backend) {
      backendEl.textContent = data.backend === 'sqlite' ? 'SQLite (dev)' : 'PostgreSQL';
    }
    if (timeEl && data.checked_at) {
      const d = new Date(data.checked_at);
      timeEl.textContent = `@ ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}`;
    }
  } catch (err) {
    console.warn('Failed to query database size:', err);
    valEl.textContent = 'Err';
  }
  const sideDb = document.getElementById('sidebar-db-size-display');
  if (sideDb && valEl) sideDb.textContent = valEl.textContent;
}

async function refreshDbSize() {
  const btn = document.getElementById('refresh-db-btn');
  if (btn) {
    btn.disabled = true;
    btn.textContent = '⌛...';
  }
  await fetchDbSize();
  if (btn) {
    btn.disabled = false;
    btn.textContent = '🔄 Refresh';
  }
}

// Populate the dashboard once immediately; SSE then keeps it current.
async function hydrateDashboard() {
  try {
    const response = await fetch('/api/market/state', { cache: 'no-store' });
    if (response.ok) updateDashboard(await response.json());
  } catch (error) {
    console.warn('Initial dashboard snapshot failed:', error);
  }
  fetchDbSize();
}

hydrateDashboard();

// =============================================================================
// Sidebar Navigation & Tab Switching
// =============================================================================
let activeTab = 'dashboard';

function switchTab(tabName, event) {
  if (event) event.preventDefault();
  activeTab = tabName;

  ['dashboard', 'wallet', 'settings', 'api'].forEach(t => {
    const navBtn = document.getElementById(`nav-${t}`);
    const pane = document.getElementById(`view-${t}`);
    if (navBtn) {
      if (t === tabName) navBtn.classList.add('active');
      else navBtn.classList.remove('active');
    }
    if (pane) {
      if (t === tabName) {
        pane.style.display = 'block';
        pane.classList.add('active');
      } else {
        pane.style.display = 'none';
        pane.classList.remove('active');
      }
    }
  });

  if (tabName === 'wallet') {
    loadWalletData();
  } else if (tabName === 'settings') {
    loadSettingsData();
  } else if (tabName === 'api') {
    initApiInspector();
  }

  try {
    history.replaceState(null, '', `#${tabName}`);
  } catch (_) {}
}

// =============================================================================
// Toast Notifications & Helpers
// =============================================================================
function showToast(msg, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => {
    toast.remove();
  }, 4000);
}

function copyText(text, btn) {
  if (!text || text.includes('---')) return;
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.innerText;
    btn.innerText = '✅ Copied!';
    setTimeout(() => { btn.innerText = orig; }, 1500);
  }).catch(() => {
    showToast('Failed to copy to clipboard', 'error');
  });
}

function toggleInputVisibility(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    btn.innerText = '🔒';
  } else {
    input.type = 'password';
    btn.innerText = '👁️';
  }
}

// =============================================================================
// Wallet Controller
// =============================================================================
let unmaskedWalletCache = null;
let isL1Revealed = false;
let isLighterRevealed = false;

async function loadWalletData() {
  try {
    const res = await fetch('/api/wallet', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderWalletView(data);
  } catch (err) {
    console.warn('Failed to load wallet data:', err);
    showToast('Failed to load wallet data', 'error');
  }
}

function renderWalletView(data) {
  const addr = data.address || '';
  const balances = data.balances || {};
  const collateral = balances.lighter_collateral_usd ?? 0.0;
  const freeMargin = balances.lighter_free_margin_usd ?? 0.0;
  const arbEth = balances.arbitrum_eth ?? 0.0;
  const accIdx = data.lighter_account_index || balances.lighter_account_index;
  const accStatus = balances.lighter_account_status || (accIdx ? 'ACTIVE' : 'UNREGISTERED');

  const collEl = document.getElementById('wallet-lighter-collateral');
  if (collEl) collEl.innerText = `$${fmt(collateral, 2)}`;
  
  const freeEl = document.getElementById('wallet-lighter-free');
  if (freeEl) freeEl.innerText = `$${fmt(freeMargin, 2)}`;
  
  const accIdxEl = document.getElementById('wallet-lighter-acc-idx');
  if (accIdxEl) accIdxEl.innerText = accIdx ? `#${accIdx}` : 'None';
  
  const lighterStatusBadge = document.getElementById('wallet-lighter-status');
  if (lighterStatusBadge) {
    lighterStatusBadge.innerText = accStatus;
    lighterStatusBadge.className = `badge ${accStatus === 'ACTIVE' ? 'badge-success' : 'badge-warning'}`;
  }

  const ethEl = document.getElementById('wallet-arb-eth');
  if (ethEl) ethEl.innerText = `${fmt(arbEth, 6)} ETH`;
  
  const syncEl = document.getElementById('wallet-last-checked');
  if (syncEl) syncEl.innerText = `Sync: ${balances.last_checked || 'Just now'}`;

  // Readiness
  const isFunded = collateral > 0;
  const readyVal = document.getElementById('wallet-readiness-val');
  const readyMsg = document.getElementById('wallet-readiness-msg');
  const readyBadge = document.getElementById('wallet-readiness-badge');
  if (readyVal && readyMsg && readyBadge) {
    if (isFunded) {
      readyVal.innerText = 'Ready for Live Trading';
      readyVal.style.color = 'var(--green)';
      readyMsg.innerText = `Collateral active: $${fmt(collateral, 2)} USDC on zkLighter`;
      readyBadge.innerText = 'REAL READY';
      readyBadge.className = 'badge badge-success';
    } else {
      readyVal.innerText = 'Awaiting Collateral';
      readyVal.style.color = 'var(--orange)';
      readyMsg.innerText = 'Deposit USDC on Lighter to enable live trading';
      readyBadge.innerText = 'SIMULATION ONLY';
      readyBadge.className = 'badge badge-warning';
    }
  }

  // Credentials
  const addrEl = document.getElementById('wallet-l1-address');
  if (addrEl) addrEl.innerText = addr || '0x--';
  
  const privEl = document.getElementById('wallet-l1-privkey');
  if (privEl && !isL1Revealed) {
    privEl.innerText = data.private_key || '••••••••••••••••••••••••••••••••';
  }
  
  const pubEl = document.getElementById('wallet-lighter-pubkey');
  if (pubEl) pubEl.innerText = data.lighter_public_key || '0x--';
  
  const lprivEl = document.getElementById('wallet-lighter-privkey');
  if (lprivEl && !isLighterRevealed) {
    lprivEl.innerText = data.lighter_private_key || '••••••••••••••••••••••••••••••••';
  }
}

async function refreshWalletData() {
  showToast('Refreshing on-chain balances...', 'info');
  try {
    const res = await fetch('/api/wallet/refresh', { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderWalletView(data);
    showToast('Balances updated successfully!', 'success');
  } catch (err) {
    showToast(`Failed to refresh balances: ${err.message}`, 'error');
  }
}

async function getUnmaskedWallet() {
  if (!unmaskedWalletCache) {
    const res = await fetch('/api/wallet/reveal');
    if (!res.ok) throw new Error('Failed to fetch credentials');
    unmaskedWalletCache = await res.json();
  }
  return unmaskedWalletCache;
}

async function toggleRevealKey(type, btn) {
  try {
    const unmasked = await getUnmaskedWallet();
    if (type === 'l1') {
      const el = document.getElementById('wallet-l1-privkey');
      if (!isL1Revealed) {
        el.innerText = unmasked.private_key;
        btn.innerText = '🔒 Hide';
        isL1Revealed = true;
      } else {
        el.innerText = '••••••••••••••••••••••••••••••••';
        btn.innerText = '👁️ Show';
        isL1Revealed = false;
      }
    } else if (type === 'lighter') {
      const el = document.getElementById('wallet-lighter-privkey');
      if (!isLighterRevealed) {
        el.innerText = unmasked.lighter_private_key;
        btn.innerText = '🔒 Hide';
        isLighterRevealed = true;
      } else {
        el.innerText = '••••••••••••••••••••••••••••••••';
        btn.innerText = '👁️ Show';
        isLighterRevealed = false;
      }
    }
  } catch (err) {
    showToast('Error revealing key', 'error');
  }
}

async function copyUnmaskedKey(keyName, btn) {
  try {
    const unmasked = await getUnmaskedWallet();
    const keyVal = unmasked[keyName];
    if (keyVal) {
      copyText(keyVal, btn);
    }
  } catch (err) {
    showToast('Failed to copy key', 'error');
  }
}

async function confirmGenerateWallet() {
  if (!confirm('⚠️ WARNING: Generate a brand new server wallet?\n\nThis will replace the current wallet keys. Make sure you have exported your existing private key if it holds funds!')) {
    return;
  }
  try {
    const res = await fetch('/api/wallet/generate', { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    unmaskedWalletCache = null;
    isL1Revealed = false;
    isLighterRevealed = false;
    renderWalletView(data);
    showToast('Generated new server wallet successfully!', 'success');
  } catch (err) {
    showToast(`Failed to generate wallet: ${err.message}`, 'error');
  }
}

function showImportWalletModal() {
  document.getElementById('import-privkey-input').value = '';
  document.getElementById('import-error-msg').innerText = '';
  document.getElementById('import-wallet-modal').style.display = 'flex';
}

function closeImportWalletModal() {
  document.getElementById('import-wallet-modal').style.display = 'none';
}

async function submitImportWallet() {
  const input = document.getElementById('import-privkey-input');
  const errEl = document.getElementById('import-error-msg');
  const privKey = (input.value || '').trim();

  if (!privKey || privKey.length < 32) {
    errEl.innerText = 'Please enter a valid Ethereum private key (hex format).';
    return;
  }

  try {
    errEl.innerText = 'Importing...';
    const res = await fetch('/api/wallet/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ private_key: privKey }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Import failed');
    
    unmaskedWalletCache = null;
    isL1Revealed = false;
    isLighterRevealed = false;
    renderWalletView(data);
    closeImportWalletModal();
    showToast('Wallet imported successfully!', 'success');
  } catch (err) {
    errEl.innerText = err.message;
  }
}

// =============================================================================
// Settings Controller
// =============================================================================
let selectedMode = 'SIMULATION';

async function loadSettingsData() {
  try {
    const res = await fetch('/api/settings', { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderSettingsView(data);
  } catch (err) {
    console.warn('Failed to load settings:', err);
    showToast('Failed to load settings', 'error');
  }
}

function renderSettingsView(data) {
  selectedMode = data.trading_mode || 'SIMULATION';
  selectTradingMode(selectedMode, false);

  const netEl = document.getElementById('setting-network');
  if (netEl) netEl.value = data.network || 'mainnet';

  const accEl = document.getElementById('setting-acc-idx');
  if (accEl) accEl.value = data.account_index > 0 ? data.account_index : '';

  const keyEl = document.getElementById('setting-key-idx');
  if (keyEl) keyEl.value = data.api_key_index ?? 4;

  const privEl = document.getElementById('setting-api-privkey');
  if (privEl) privEl.value = '';

  const marginEl = document.getElementById('setting-margin-pct');
  if (marginEl) marginEl.value = Math.round((data.trade_margin_fraction ?? 0.50) * 100);

  const levEl = document.getElementById('setting-leverage');
  if (levEl) levEl.value = data.leverage ?? 50;

  const minLagEl = document.getElementById('setting-min-lag');
  if (minLagEl) minLagEl.value = data.min_lag_trigger ?? 6.0;
  if (data.min_lag_trigger !== undefined) updateLagChartThresholds(data.min_lag_trigger);

  const maxHoldEl = document.getElementById('setting-max-hold');
  if (maxHoldEl) maxHoldEl.value = data.max_hold_seconds ?? 12.0;

  const simBalEl = document.getElementById('setting-sim-starting-balance');
  if (simBalEl) simBalEl.value = data.simulation_starting_balance ?? 100.0;

  const alertEl = document.getElementById('mode-status-alert');
  if (alertEl) {
    if (data.is_real_eligible) {
      alertEl.className = 'settings-alert success';
      alertEl.innerText = `✅ Account ready for REAL mode on Lighter ${data.network.toUpperCase()}!`;
    } else {
      alertEl.className = 'settings-alert warning';
      alertEl.innerText = `ℹ️ ${data.eligibility_message || 'Setup wallet and Lighter account to enable REAL trading.'}`;
    }
  }
}

function selectTradingMode(mode, showWarning = true) {
  selectedMode = mode;
  const simCard = document.getElementById('mode-card-sim');
  const realCard = document.getElementById('mode-card-real');

  if (simCard && realCard) {
    if (mode === 'REAL') {
      simCard.classList.remove('active');
      realCard.classList.add('active');
      if (showWarning) {
        showToast('Selected REAL Mode: Click Save & Apply to activate live trading.', 'info');
      }
    } else {
      realCard.classList.remove('active');
      simCard.classList.add('active');
    }
  }
}

async function saveSystemSettings() {
  const btn = document.getElementById('save-settings-btn');
  if (btn) {
    btn.disabled = true;
    btn.innerText = '⌛ Saving...';
  }

  const payload = {
    trading_mode: selectedMode,
    network: document.getElementById('setting-network').value,
    account_index: document.getElementById('setting-acc-idx').value ? parseInt(document.getElementById('setting-acc-idx').value) : null,
    api_key_index: parseInt(document.getElementById('setting-key-idx').value) || 4,
    trade_margin_fraction: parseFloat(document.getElementById('setting-margin-pct').value) / 100.0,
    leverage: parseFloat(document.getElementById('setting-leverage').value) || 50.0,
    min_lag_trigger: parseFloat(document.getElementById('setting-min-lag').value) || 6.0,
    max_hold_seconds: parseFloat(document.getElementById('setting-max-hold').value) || 12.0,
  };

  const simBalInput = document.getElementById('setting-sim-starting-balance');
  if (simBalInput && !isNaN(parseFloat(simBalInput.value))) {
    payload.simulation_starting_balance = parseFloat(simBalInput.value);
  }

  const privInput = document.getElementById('setting-api-privkey').value.trim();
  if (privInput) {
    payload.api_private_key = privInput;
  }

  try {
    const res = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Failed to save settings');

    renderSettingsView(data);
    showToast(`Settings saved! Execution mode: ${data.trading_mode}`, 'success');
  } catch (err) {
    showToast(`Error saving settings: ${err.message}`, 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerText = '💾 Save & Apply';
    }
  }
}

async function confirmResetSimulation() {
  const simBal = parseFloat(document.getElementById('setting-sim-starting-balance')?.value) || 100.0;
  const confirmed = confirm(
    `Are you sure you want to reset the simulation system?\n\n` +
    `• All paper trades and history will be cleared.\n` +
    `• Performance and PnL will be reset to 0.\n` +
    `• Simulation account balance will be restarted at $${simBal.toFixed(2)}.\n` +
    `• A new simulation run will be started from time 0.`
  );
  if (!confirmed) return;

  const feedbackEl = document.getElementById('reset-sim-feedback');
  if (feedbackEl) feedbackEl.innerText = 'Resetting system...';

  try {
    // 1. Save configured starting balance first if updated
    await saveSystemSettings();

    // 2. Call reset endpoint
    const res = await fetch('/api/system/reset-simulation', { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    showToast(`Simulation reset! Starting balance: $${fmt(data.starting_balance, 2)}`, 'success');
    if (feedbackEl) {
      feedbackEl.innerText = `✅ Reset complete at ${data.reset_at ? new Date(data.reset_at).toLocaleTimeString() : 'now'}`;
    }

    // Clear charts and reset table rows
    if (priceChart) priceChart.clear();
    if (lagChart) lagChart.clear();
    const tradesBody = document.getElementById('trades-table-body');
    if (tradesBody) tradesBody.innerHTML = '<tr><td colspan="8" style="color: var(--text-muted); text-align: center;">Awaiting trades...</td></tr>';
    const repricingBody = document.getElementById('repricing-table-body');
    if (repricingBody) repricingBody.innerHTML = '<tr><td colspan="5" style="color: var(--text-muted); text-align: center;">Monitoring breakout repricing cycles...</td></tr>';
  } catch (err) {
    showToast(`Reset failed: ${err.message}`, 'error');
    if (feedbackEl) feedbackEl.innerText = `❌ Error: ${err.message}`;
  }
}

// Initial hash routing
window.addEventListener('DOMContentLoaded', () => {
  const hash = window.location.hash.replace('#', '');
  if (['dashboard', 'wallet', 'settings'].includes(hash)) {
    switchTab(hash);
  }
});
