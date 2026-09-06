import { currencyTick, setText } from './ui-utils.js';

let priceChart = null;
let lagChart = null;
let latestChart = null;
let latestProviders = null;
let visible = true;

function createCharts() {
  if (typeof window.CanvasLineChart !== 'function') return;
  priceChart = new window.CanvasLineChart(document.getElementById('priceChart'), {
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
  });
  lagChart = new window.CanvasLineChart(document.getElementById('lagChart'), {
    datasets: [{ id: 'lighterLag', label: 'Lighter vs leader', color: '#f59e0b', lineWidth: 2.5 }],
    thresholds: defaultThresholds(6),
    yFormatter: (value) => `${value >= 0 ? '+' : ''}$${value.toFixed(2)}`,
    emptyMessage: 'Waiting for Lighter and leader samples…',
  });
}

function defaultThresholds(minLag) {
  const value = Math.abs(Number(minLag)) || 6;
  return [
    { value, label: `SHORT SNIPE (+$${value.toFixed(1)})`, color: '#ff4d6d' },
    { value: 0, label: 'PARITY ($0)', color: '#64748b' },
    { value: -value, label: `LONG SNIPE (-$${value.toFixed(1)})`, color: '#10e598' },
  ];
}

function drawLatest() {
  if (!visible || document.hidden || !latestChart) return;
  const timestamps = Array.isArray(latestChart.timestamps) ? latestChart.timestamps : [];
  priceChart?.setData(timestamps, {
    binance: latestChart.binance_series,
    bybit: latestChart.bybit_series,
    okx: latestChart.okx_series,
    hyperliquid: latestChart.hl_series,
    polymarket: latestChart.poly_series,
    lighter: latestChart.lighter_series,
  });
  lagChart?.setData(timestamps, { lighterLag: latestChart.lighter_lag_series });
  const providers = latestProviders?.providers || [];
  const fresh = providers.filter((provider) => provider.fresh).length;
  const cadence = latestChart.sample_interval_ms ? ` · ${latestChart.sample_interval_ms}ms source cadence` : '';
  const status = timestamps.length
    ? `${timestamps.length}/${latestChart.max_points || timestamps.length} display samples${cadence}`
    : `No sample yet · ${fresh}/${providers.length || 6} providers fresh`;
  setText('price-chart-status', priceChart ? status : 'Local chart renderer unavailable');
  setText('lag-chart-status', lagChart ? status : 'Local chart renderer unavailable');
}

function syncVisibility() {
  const paused = !visible || document.hidden;
  priceChart?.setPaused(paused);
  lagChart?.setPaused(paused);
  if (!paused) drawLatest();
}

export function initialiseCharts() {
  createCharts();
  document.addEventListener('visibilitychange', syncVisibility);
}

export function renderCharts(chart, providerInsights) {
  latestChart = chart || latestChart;
  latestProviders = providerInsights || latestProviders;
  drawLatest();
}

export function setChartsVisible(nextVisible) {
  visible = Boolean(nextVisible);
  syncVisibility();
}

export function updateLagChartThresholds(minLag) {
  lagChart?.setThresholds(defaultThresholds(minLag));
  const value = Math.abs(Number(minLag)) || 6;
  setText('lag-chart-title', `LIGHTER.XYZ LAG VS DYNAMIC LEADER & SNIPE THRESHOLDS (±$${value.toFixed(2)})`);
}

export function clearCharts() {
  latestChart = null;
  priceChart?.clear();
  lagChart?.clear();
}
