import { memo } from 'preact/compat';
import { useEffect, useRef } from 'preact/hooks';

function currencyTick(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '--';
  return `$${parsed.toLocaleString('en-US', { maximumFractionDigits: Math.abs(parsed) >= 1000 ? 0 : 2 })}`;
}

function thresholds(minLag) {
  const value = Math.abs(Number(minLag)) || 6;
  return [
    { value, label: `SHORT SNIPE (+$${value.toFixed(1)})`, color: '#ff4d6d' },
    { value: 0, label: 'PARITY ($0)', color: '#64748b' },
    { value: -value, label: `LONG SNIPE (-$${value.toFixed(1)})`, color: '#10e598' },
  ];
}

function chartStatus(chart, providers) {
  const timestamps = Array.isArray(chart?.timestamps) ? chart.timestamps : [];
  const fresh = (providers?.providers || []).filter((provider) => provider.fresh).length;
  const cadence = chart?.sample_interval_ms ? ` · ${chart.sample_interval_ms}ms source cadence` : '';
  return timestamps.length
    ? `${timestamps.length}/${chart.max_points || timestamps.length} display samples${cadence}`
    : `No sample yet · ${fresh}/${providers?.providers?.length || 6} providers fresh`;
}

function CanvasPanel({ chart, kind, minLag }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    const CanvasLineChart = window.CanvasLineChart;
    if (!CanvasLineChart || !canvasRef.current) return undefined;
    const options = kind === 'price'
      ? {
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
      }
      : {
        datasets: [{ id: 'lighterLag', label: 'Lighter vs leader', color: '#f59e0b', lineWidth: 2.5 }],
        thresholds: thresholds(minLag),
        yFormatter: (value) => `${value >= 0 ? '+' : ''}$${value.toFixed(2)}`,
        emptyMessage: 'Waiting for Lighter and leader samples…',
      };
    const instance = new CanvasLineChart(canvasRef.current, options);
    chartRef.current = instance;
    const syncVisibility = () => instance.setPaused(document.hidden);
    document.addEventListener('visibilitychange', syncVisibility);
    syncVisibility();
    return () => {
      document.removeEventListener('visibilitychange', syncVisibility);
      instance.destroy?.();
      chartRef.current = null;
    };
  }, [kind]);

  useEffect(() => {
    const instance = chartRef.current;
    if (!instance || !chart) return;
    const timestamps = Array.isArray(chart.timestamps) ? chart.timestamps : [];
    if (kind === 'price') {
      instance.setData(timestamps, {
        binance: chart.binance_series,
        bybit: chart.bybit_series,
        okx: chart.okx_series,
        hyperliquid: chart.hl_series,
        polymarket: chart.poly_series,
        lighter: chart.lighter_series,
      });
    } else {
      instance.setThresholds(thresholds(minLag));
      instance.setData(timestamps, { lighterLag: chart.lighter_lag_series });
    }
  }, [chart, kind, minLag]);

  return <canvas ref={canvasRef} aria-label={`${kind} chart`} />;
}

function Charts({ chart, providers, minLag }) {
  const status = chartStatus(chart, providers);
  const threshold = Math.abs(Number(minLag)) || 6;
  const hasSamples = Array.isArray(chart?.timestamps) && chart.timestamps.length > 0;
  const waitingStatus = hasSamples ? status : 'Waiting for first live sample';
  return (
    <div class="right-divided-col">
      <div class="chart-panel-block">
        <div class="chart-header-row">
          <div class="chart-title">6-WAY REAL-TIME PRICE OVERLAY (BTC PERPETUALS)</div>
          <span class="chart-status-pill mono">{waitingStatus}</span>
        </div>
        <div class="chart-viewport"><CanvasPanel chart={chart} kind="price" minLag={minLag} /></div>
      </div>
      <div class="chart-panel-block">
        <div class="chart-header-row">
          <div class="chart-title">LIGHTER.XYZ LAG VS DYNAMIC LEADER &amp; SNIPE THRESHOLDS (±${threshold.toFixed(2)})</div>
          <span class="chart-status-pill mono">{waitingStatus}</span>
        </div>
        <div class="chart-viewport"><CanvasPanel chart={chart} kind="lag" minLag={minLag} /></div>
      </div>
    </div>
  );
}

export const DashboardCharts = memo(Charts);
