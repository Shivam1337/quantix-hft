import { useMemo, useState } from "react";
import type { FundingSnapshot } from "../types";

type Props = {
  records: FundingSnapshot[];
  symbol: string;
  venue: string;
};

export function FundingHistoryChart({ records, symbol, venue }: Props) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);

  // Chronological order (oldest to newest)
  const sorted = useMemo(
    () => [...records].sort((a, b) => new Date(a.observed_at).getTime() - new Date(b.observed_at).getTime()),
    [records]
  );

  const stats = useMemo(() => {
    if (!sorted.length) return null;
    const rates = sorted.map((r) => r.funding_rate);
    const minRate = Math.min(...rates);
    const maxRate = Math.max(...rates);
    const avgRate = rates.reduce((a, b) => a + b, 0) / rates.length;
    const latest = sorted[sorted.length - 1];
    return { minRate, maxRate, avgRate, latest };
  }, [sorted]);

  if (!sorted.length || !stats) {
    return (
      <div className="panel p-6 text-center text-sm text-slate-500">
        No historical records found for {venue.toUpperCase()} · {symbol}
      </div>
    );
  }

  // Chart coordinates calculation
  const width = 800;
  const height = 220;
  const padding = { top: 20, right: 30, bottom: 30, left: 55 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  const rates = sorted.map((r) => r.funding_rate);
  const minVal = Math.min(0, ...rates);
  const maxVal = Math.max(0, ...rates);
  const range = maxVal - minVal || 0.0001;

  const getY = (val: number) => padding.top + chartH - ((val - minVal) / range) * chartH;
  const getX = (idx: number) => padding.left + (idx / Math.max(sorted.length - 1, 1)) * chartW;

  const zeroY = getY(0);
  const points = sorted.map((r, i) => `${getX(i)},${getY(r.funding_rate)}`).join(" ");

  const hovered = hoveredIdx !== null ? sorted[hoveredIdx] : null;

  return (
    <div className="panel overflow-hidden">
      <div className="section-heading">
        <div>
          <p className="eyebrow text-cyan">Historical time-series</p>
          <h2>{symbol} Funding Rate History ({venue.toUpperCase()})</h2>
        </div>
        <div className="flex items-center gap-3 text-xs">
          <span className="text-slate-400">{sorted.length} recorded cycles</span>
          <span className="count-badge">{(stats.latest.funding_rate * 100).toFixed(4)}%/h</span>
        </div>
      </div>

      {/* Summary Metrics Bar */}
      <div className="grid grid-cols-2 gap-px border-b border-slate-800 bg-slate-800 sm:grid-cols-4">
        <div className="bg-slate-900/50 p-3">
          <span className="text-[10px] uppercase text-slate-500">Current Rate / APR</span>
          <p className="font-mono text-sm font-semibold text-cyan">
            {(stats.latest.funding_rate * 100).toFixed(4)}% · {(stats.latest.funding_rate * 24 * 365 * 100).toFixed(1)}% APR
          </p>
        </div>
        <div className="bg-slate-900/50 p-3">
          <span className="text-[10px] uppercase text-slate-500">Average Rate / APR</span>
          <p className="font-mono text-sm font-semibold text-slate-200">
            {(stats.avgRate * 100).toFixed(4)}% · {(stats.avgRate * 24 * 365 * 100).toFixed(1)}% APR
          </p>
        </div>
        <div className="bg-slate-900/50 p-3">
          <span className="text-[10px] uppercase text-slate-500">Min Rate</span>
          <p className={`font-mono text-sm font-semibold ${stats.minRate >= 0 ? "text-emerald-300" : "text-rose-400"}`}>
            {(stats.minRate * 100).toFixed(4)}%
          </p>
        </div>
        <div className="bg-slate-900/50 p-3">
          <span className="text-[10px] uppercase text-slate-500">Max Rate</span>
          <p className={`font-mono text-sm font-semibold ${stats.maxRate >= 0 ? "text-emerald-300" : "text-rose-400"}`}>
            {(stats.maxRate * 100).toFixed(4)}%
          </p>
        </div>
      </div>

      {/* Interactive SVG Chart */}
      <div className="relative p-4">
        {hovered && hoveredIdx !== null && (
          <div
            className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-lg border border-slate-700 bg-slate-900/95 px-3 py-1.5 shadow-xl backdrop-blur-sm"
            style={{ left: `${(getX(hoveredIdx) / width) * 100}%`, top: "20px" }}
          >
            <div className="text-[10px] text-slate-400">{new Date(hovered.observed_at).toLocaleString()}</div>
            <div className="flex items-center gap-2 font-mono text-xs">
              <span className={hovered.funding_rate >= 0 ? "text-emerald-300" : "text-rose-400"}>
                {(hovered.funding_rate * 100).toFixed(4)}%/h
              </span>
              <span className="text-slate-500">·</span>
              <span className="text-slate-200">${hovered.mark_price.toLocaleString()}</span>
            </div>
          </div>
        )}

        <svg viewBox={`0 0 ${width} ${height}`} className="w-full overflow-visible">
          {/* Grid lines */}
          <line x1={padding.left} y1={zeroY} x2={width - padding.right} y2={zeroY} stroke="#334155" strokeDasharray="3 3" />
          <line x1={padding.left} y1={padding.top} x2={padding.left} y2={height - padding.bottom} stroke="#1e293b" />
          <line x1={padding.left} y1={height - padding.bottom} x2={width - padding.right} y2={height - padding.bottom} stroke="#1e293b" />

          {/* Y Axis Labels */}
          <text x={padding.left - 8} y={padding.top + 4} fill="#64748b" fontSize="9" textAnchor="end" fontFamily="monospace">
            {(maxVal * 100).toFixed(3)}%
          </text>
          <text x={padding.left - 8} y={zeroY + 3} fill="#64748b" fontSize="9" textAnchor="end" fontFamily="monospace">
            0.000%
          </text>
          <text x={padding.left - 8} y={height - padding.bottom} fill="#64748b" fontSize="9" textAnchor="end" fontFamily="monospace">
            {(minVal * 100).toFixed(3)}%
          </text>

          {/* X Axis Range Labels */}
          <text x={padding.left} y={height - 10} fill="#64748b" fontSize="9" textAnchor="start">
            {new Date(sorted[0].observed_at).toLocaleDateString()}
          </text>
          <text x={width - padding.right} y={height - 10} fill="#64748b" fontSize="9" textAnchor="end">
            {new Date(sorted[sorted.length - 1].observed_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </text>

          {/* Rate Curve */}
          <polyline fill="none" stroke="#22d3ee" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" points={points} />

          {/* Data Points */}
          {sorted.map((r, i) => {
            const isHovered = hoveredIdx === i;
            return (
              <circle
                key={r.id || i}
                cx={getX(i)}
                cy={getY(r.funding_rate)}
                r={isHovered ? 5 : 3}
                fill={r.funding_rate >= 0 ? "#34d399" : "#f43f5e"}
                stroke="#0f172a"
                strokeWidth={1.5}
                className="cursor-pointer transition-all"
                onMouseEnter={() => setHoveredIdx(i)}
                onMouseLeave={() => setHoveredIdx(null)}
              />
            );
          })}
        </svg>
      </div>
    </div>
  );
}
