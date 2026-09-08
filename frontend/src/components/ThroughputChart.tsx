import { useMemo, useState } from "react";
import type { ThroughputMinute } from "../types";

type Props = {
  history: ThroughputMinute[];
  restHistory?: ThroughputMinute[];
  venues: string[];
  mode?: "ws" | "rest";
  onModeChange?: (mode: "ws" | "rest") => void;
};

const VENUE_COLORS: Record<string, string> = {
  hyperliquid: "#34d399",
  aevo: "#c084fc",
  lighter: "#22d3ee",
};

export function ThroughputChart({
  history,
  restHistory = [],
  venues,
  mode: propMode,
  onModeChange,
}: Props) {
  const [internalMode, setInternalMode] = useState<"ws" | "rest">("ws");
  const mode = propMode ?? internalMode;
  const isRest = mode === "rest";
  const activeHistory = isRest ? (restHistory.length > 0 ? restHistory : history) : history;

  const handleModeChange = (next: "ws" | "rest") => {
    setInternalMode(next);
    onModeChange?.(next);
  };

  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const [activeVenues, setActiveVenues] = useState<Record<string, boolean>>({
    hyperliquid: true,
    aevo: true,
    lighter: true,
  });

  const width = 820;
  const height = 240;
  const padding = { top: 25, right: 30, bottom: 35, left: 55 };
  const chartW = width - padding.left - padding.right;
  const chartH = height - padding.top - padding.bottom;

  const maxCount = useMemo(() => {
    let max = isRest ? 5 : 10;
    activeHistory.forEach((h) => {
      venues.forEach((v) => {
        if (activeVenues[v]) {
          max = Math.max(max, h.counts[v] ?? 0);
        }
      });
    });
    return Math.max(Math.ceil(max * 1.15), isRest ? 4 : 10);
  }, [activeHistory, venues, activeVenues, isRest]);

  const getY = (val: number) => padding.top + chartH - (val / maxCount) * chartH;
  const getX = (idx: number) => padding.left + (idx / Math.max(activeHistory.length - 1, 1)) * chartW;

  const hovered = hoveredIdx !== null ? activeHistory[hoveredIdx] : null;
  const unit = isRest ? "calls/m" : "msg/m";

  const toggleVenue = (v: string) => {
    setActiveVenues((prev) => ({ ...prev, [v]: !prev[v] }));
  };

  return (
    <div className="panel overflow-hidden">
      <div className="section-heading flex-wrap gap-4">
        <div>
          <p className="eyebrow text-cyan">{isRest ? "REST Telemetry" : "Live Telemetry"}</p>
          <h2>{isRest ? "REST API Throughput (Calls / Minute)" : "WebSocket Throughput (Messages / Minute)"}</h2>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {/* Mode Switcher */}
          <div className="flex rounded-lg border border-slate-800 bg-slate-900/60 p-0.5 text-xs">
            <button
              type="button"
              onClick={() => handleModeChange("ws")}
              className={`rounded-md px-2.5 py-1 font-medium transition-colors ${
                !isRest ? "bg-cyan text-slate-950 font-bold" : "text-slate-400 hover:text-white"
              }`}
            >
              WebSocket
            </button>
            <button
              type="button"
              onClick={() => handleModeChange("rest")}
              className={`rounded-md px-2.5 py-1 font-medium transition-colors ${
                isRest ? "bg-cyan text-slate-950 font-bold" : "text-slate-400 hover:text-white"
              }`}
            >
              REST Calls
            </button>
          </div>

          {/* Venue toggles */}
          <div className="flex flex-wrap items-center gap-1.5">
            {venues.map((v) => {
              const active = activeVenues[v] ?? true;
              const color = VENUE_COLORS[v] ?? "#94a3b8";
              return (
                <button
                  key={v}
                  type="button"
                  onClick={() => toggleVenue(v)}
                  className={`flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
                    active
                      ? "border-slate-600 bg-slate-800 text-white"
                      : "border-slate-800 bg-slate-900/40 text-slate-500 opacity-60"
                  }`}
                >
                  <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
                  <span className="capitalize">{v}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="relative p-4">
        {hovered && hoveredIdx !== null && (
          <div
            className="pointer-events-none absolute z-10 -translate-x-1/2 rounded-lg border border-slate-700 bg-slate-900/95 p-2.5 shadow-xl backdrop-blur-sm"
            style={{ left: `${(getX(hoveredIdx) / width) * 100}%`, top: "15px" }}
          >
            <div className="text-[10px] text-slate-400">
              {new Date(hovered.timestamp).toLocaleTimeString()}
            </div>
            <div className="mt-1 space-y-0.5 font-mono text-xs">
              {venues.map((v) => (
                <div key={v} className="flex items-center justify-between gap-4">
                  <span className="capitalize" style={{ color: VENUE_COLORS[v] }}>{v}:</span>
                  <span className="font-bold text-white">{hovered.counts[v] ?? 0} {unit}</span>
                </div>
              ))}
              <div className="mt-1 border-t border-slate-800 pt-1 text-[11px] text-slate-300">
                Total: <strong>{hovered.total} {unit}</strong>
              </div>
            </div>
          </div>
        )}

        <svg viewBox={`0 0 ${width} ${height}`} className="w-full overflow-visible">
          {[0, 0.25, 0.5, 0.75, 1].map((ratio) => {
            const val = Math.round(maxCount * ratio);
            const y = getY(val);
            return (
              <g key={ratio}>
                <line x1={padding.left} y1={y} x2={width - padding.right} y2={y} stroke="#1e293b" strokeDasharray="3 3" />
                <text x={padding.left - 8} y={y + 3} fill="#64748b" fontSize="9" textAnchor="end" fontFamily="monospace">
                  {val}
                </text>
              </g>
            );
          })}

          <line x1={padding.left} y1={height - padding.bottom} x2={width - padding.right} y2={height - padding.bottom} stroke="#334155" />
          {activeHistory.length > 0 && (
            <>
              <text x={padding.left} y={height - 12} fill="#64748b" fontSize="9">
                {new Date(activeHistory[0].timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </text>
              <text x={width - padding.right} y={height - 12} fill="#64748b" fontSize="9" textAnchor="end">
                {new Date(activeHistory[activeHistory.length - 1].timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </text>
            </>
          )}

          {venues.map((v) => {
            if (!activeVenues[v]) return null;
            const points = activeHistory.map((h, i) => `${getX(i)},${getY(h.counts[v] ?? 0)}`).join(" ");
            const color = VENUE_COLORS[v] ?? "#94a3b8";

            return (
              <g key={v}>
                <polyline fill="none" stroke={color} strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" points={points} />
                {activeHistory.map((h, i) => {
                  const val = h.counts[v] ?? 0;
                  const isHovered = hoveredIdx === i;
                  return (
                    <circle
                      key={i}
                      cx={getX(i)}
                      cy={getY(val)}
                      r={isHovered ? 4.5 : 2}
                      fill={color}
                      className="cursor-pointer transition-all"
                      onMouseEnter={() => setHoveredIdx(i)}
                      onMouseLeave={() => setHoveredIdx(null)}
                    />
                  );
                })}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
