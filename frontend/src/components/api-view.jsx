import { useEffect, useState } from 'preact/hooks';
import { copyToClipboard } from '../lib/http.js';
import { useToast } from '../ui/toasts.jsx';

const CATEGORIES = [
  ['📈 Market & Pricing', [
    ['/api/market/prices', 'Rapid 6-Way Prices'], ['/api/analytics/lead-lag', '5-Way Consensus & Leader'],
    ['/api/system/providers', 'Provider Insights & Freshness'], ['/api/analytics/fees-comparison', 'Fee Hurdles Breakdown'],
  ]],
  ['⚡ Trading & Execution', [
    ['/api/trades/decision', 'Trade Decision & Stance'], ['/api/trades/performance', 'Trading Performance & PnL'],
    ['/api/trades/history', 'Recent Sniper Trades'], ['/api/trades/comparisons', 'DUAL Simulated vs Real Executions'],
    ['/api/analytics/repricing-events', 'Repricing Catch-Up Cycles'],
  ]],
  ['⚙️ System & State', [
    ['/api/wallet', 'Wallet Public Address & Balances'], ['/api/settings', 'System Configuration & Risk'],
    ['/api/system/database-size', 'Database Storage Size'], ['/api/system/resources', 'Host CPU & RAM Resources'],
    ['/api/system/persistence', 'Persistence Status'], ['/api/system/health', 'System Health Check'],
  ]],
];

export function ApiView() {
  const toast = useToast();
  const [endpoint, setEndpoint] = useState('/api/market/prices');
  const [title, setTitle] = useState('QUERY RESULT');
  const [responseText, setResponseText] = useState('Click any endpoint above or click "Send Request" to inspect a live response payload.');
  const [status, setStatus] = useState('READY');
  const [latency, setLatency] = useState('-- ms');
  const [open, setOpen] = useState(true);

  async function query(nextEndpoint, nextTitle = 'Custom Request') {
    const request = nextEndpoint.trim() || '/api/market/prices';
    setEndpoint(request); setTitle(`ENDPOINT: ${nextTitle} (${request})`); setStatus('FETCHING...'); setResponseText('Fetching live data...'); setOpen(true);
    const started = performance.now();
    try {
      const response = await fetch(request, { cache: 'no-store' });
      const raw = await response.text();
      let payload;
      try { payload = JSON.parse(raw); } catch (_) { payload = { response: raw }; }
      setResponseText(JSON.stringify(payload, null, 2));
      setStatus(`${response.status} ${response.statusText || (response.ok ? 'OK' : 'ERROR')}`);
    } catch (error) {
      setResponseText(`Error querying ${request}: ${error.message}`);
      setStatus('ERROR');
    } finally {
      setLatency(`${Math.round(performance.now() - started)} ms`);
    }
  }

  useEffect(() => { void query('/api/market/prices', 'Rapid 6-Way Prices'); }, []);

  async function copyResponse() {
    try {
      await copyToClipboard(responseText);
      toast('Response copied to clipboard.', 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function clear() {
    setTitle('QUERY RESULT'); setStatus('IDLE'); setLatency('-- ms'); setResponseText('Response cleared. Select an endpoint or click "Send Request".');
  }

  return (
    <section id="view-api" class="view-pane">
      <div class="view-content-wrapper">
        <div class="view-header"><div><div class="view-title">🔍 Instant API Inspector &amp; Query Studio</div><div class="view-subtitle">Execute and inspect live REST endpoints for prices, consensus, trades, and telemetry.</div></div><div class="view-actions"><button class="btn btn-secondary" onClick={copyResponse}>📋 Copy Response</button><button class="btn btn-secondary" onClick={clear}>✕ Clear</button></div></div>
        <form class="api-request-bar" onSubmit={(event) => { event.preventDefault(); void query(endpoint); }}><span class="api-method-badge">GET</span><input class="api-endpoint-input mono" value={endpoint} onInput={(event) => setEndpoint(event.currentTarget.value)} placeholder="/api/..." /><button class="btn btn-primary" type="submit">🚀 Send Request</button></form>
        <div class="api-categories-grid">{CATEGORIES.map(([category, endpoints]) => <div class="api-cat-card" key={category}><div class="api-cat-title">{category}</div><div class="api-cat-buttons">{endpoints.map(([path, label]) => <button class="q-btn" key={path} onClick={() => void query(path, label)}>GET {path}</button>)}</div></div>)}</div>
        {open && <div class="api-response-panel"><div class="api-response-header"><div id="json-preview-title" class="mono">{title}</div><div class="api-response-meta"><span class={`badge ${status.startsWith('2') ? 'badge-success' : status === 'ERROR' ? 'badge-danger' : 'badge-info'} mono`}>{status}</span><span class="mono" style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{latency}</span></div></div><div id="json-preview-container"><button class="close-preview-btn" onClick={() => setOpen(false)}>✕</button><pre id="json-preview-content" class="mono">{responseText}</pre></div></div>}
      </div>
    </section>
  );
}
