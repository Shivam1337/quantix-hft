import { useEffect, useState } from "react";
import { getThroughput } from "../api";
import type { ThroughputSummary } from "../types";
import { RestEndpointsTable } from "./RestEndpointsTable";
import { ThroughputChart } from "./ThroughputChart";

const VENUE_ACCENTS: Record<string, { color: string; border: string; bg: string }> = {
  hyperliquid: { color: "text-emerald-400", border: "border-emerald-500/30", bg: "bg-emerald-500/10" },
  aevo: { color: "text-purple-400", border: "border-purple-500/30", bg: "bg-purple-500/10" },
  lighter: { color: "text-cyan", border: "border-cyan/30", bg: "bg-cyan/10" },
};

export function ThroughputView() {
  const [data, setData] = useState<ThroughputSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [auditMode, setAuditMode] = useState<"ws" | "rest">("ws");

  const loadData = async () => {
    try {
      const res = await getThroughput();
      setData(res);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load telemetry");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
    const timer = window.setInterval(() => void loadData(), 3000);
    return () => window.clearInterval(timer);
  }, []);

  if (loading && !data) {
    return <div className="panel p-8 text-center text-slate-400">Loading stream telemetry…</div>;
  }

  const venues = data?.venues ?? ["hyperliquid", "aevo", "lighter"];
  const totalWsAllTime = Object.values(data?.total_processed ?? {}).reduce((a, b) => a + b, 0);
  const totalRestAllTime = Object.values(data?.rest_total_processed ?? {}).reduce((a, b) => a + b, 0);
  const currentWsRate = Object.values(data?.current_rates ?? {}).reduce((a, b) => a + b, 0);
  const currentRestRate = Object.values(data?.rest_current_rates ?? {}).reduce((a, b) => a + b, 0);

  const activeHistory = auditMode === "ws" ? (data?.history ?? []) : (data?.rest_history ?? []);

  return (
    <div className="space-y-6">
      {error && <div className="alert">{error}</div>}

      {/* Aggregate Overview Metrics */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <div className="panel p-5">
          <p className="eyebrow text-cyan">All-time WebSocket</p>
          <p className="mt-2 font-mono text-3xl font-bold text-white">
            {totalWsAllTime.toLocaleString()} <span className="text-sm font-normal text-slate-400">msgs</span>
          </p>
          <p className="mt-1 text-xs text-slate-500">{currentWsRate.toLocaleString()} msg/min live</p>
        </div>
        <div className="panel p-5">
          <p className="eyebrow text-emerald-400">All-time REST calls</p>
          <p className="mt-2 font-mono text-3xl font-bold text-emerald-300">
            {totalRestAllTime.toLocaleString()} <span className="text-sm font-normal text-slate-400">calls</span>
          </p>
          <p className="mt-1 text-xs text-slate-500">{currentRestRate.toLocaleString()} calls/min live</p>
        </div>
        <div className="panel p-5">
          <p className="eyebrow text-slate-400">Active Streams</p>
          <p className="mt-2 font-mono text-3xl font-bold text-slate-200">
            {venues.length} <span className="text-sm font-normal text-slate-400">venues</span>
          </p>
          <p className="mt-1 flex items-center gap-1.5 text-xs text-emerald-300">
            <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
            Continuous ingestion
          </p>
        </div>
        <div className="panel p-5">
          <p className="eyebrow text-purple-400">REST Polling</p>
          <p className="mt-2 font-mono text-3xl font-bold text-purple-300">
            {Object.values(data?.rest_endpoints ?? {}).flat().length} <span className="text-sm font-normal text-slate-400">endpoints</span>
          </p>
          <p className="mt-1 text-xs text-slate-500">Active REST monitors</p>
        </div>
      </div>

      {/* Per-Exchange Throughput Cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {venues.map((v) => {
          const wsRate = data?.current_rates[v] ?? 0;
          const wsTotal = data?.total_processed[v] ?? 0;
          const restRate = data?.rest_current_rates?.[v] ?? 0;
          const restTotal = data?.rest_total_processed?.[v] ?? 0;
          const styling = VENUE_ACCENTS[v] ?? { color: "text-white", border: "border-slate-800", bg: "bg-panel" };

          return (
            <div key={v} className={`rounded-2xl border p-4 ${styling.border} ${styling.bg}`}>
              <div className="flex items-center justify-between">
                <span className="font-semibold uppercase tracking-wider text-slate-200">{v}</span>
                <span className="flex items-center gap-1.5 text-[10px] text-emerald-400">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
                  Live Ingestion
                </span>
              </div>
              <div className="mt-3 grid grid-cols-2 gap-2 border-t border-slate-800/60 pt-3">
                <div>
                  <div className="text-[10px] uppercase text-slate-400">WebSocket Rate</div>
                  <div className={`font-mono text-xl font-bold ${styling.color}`}>{wsRate} <span className="text-[11px] font-normal">msg/m</span></div>
                  <div className="mt-0.5 text-[10px] text-slate-500 font-mono">{wsTotal.toLocaleString()} total</div>
                </div>
                <div>
                  <div className="text-[10px] uppercase text-slate-400">REST API Rate</div>
                  <div className="font-mono text-xl font-bold text-cyan">{restRate} <span className="text-[11px] font-normal">calls/m</span></div>
                  <div className="mt-0.5 text-[10px] text-slate-500 font-mono">{restTotal.toLocaleString()} total</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Active REST Endpoints & Request Counters */}
      <RestEndpointsTable endpoints={data?.rest_endpoints} venues={venues} />

      {/* Multi-Exchange Historical Chart */}
      {data && (
        <ThroughputChart
          history={data.history}
          restHistory={data.rest_history}
          venues={venues}
          mode={auditMode}
          onModeChange={setAuditMode}
        />
      )}

      {/* Minute-by-minute audit log */}
      {data && (
        <div className="panel overflow-hidden">
          <div className="section-heading flex items-center justify-between">
            <div>
              <p className="eyebrow text-cyan">Audit Log</p>
              <h2>Minute-by-Minute Processed Activity</h2>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex rounded-lg border border-slate-800 bg-slate-900/60 p-0.5 text-xs">
                <button
                  onClick={() => setAuditMode("ws")}
                  className={`rounded-md px-2.5 py-1 font-medium transition-colors ${
                    auditMode === "ws" ? "bg-cyan text-slate-950 font-bold" : "text-slate-400 hover:text-white"
                  }`}
                >
                  WebSocket Msgs
                </button>
                <button
                  onClick={() => setAuditMode("rest")}
                  className={`rounded-md px-2.5 py-1 font-medium transition-colors ${
                    auditMode === "rest" ? "bg-cyan text-slate-950 font-bold" : "text-slate-400 hover:text-white"
                  }`}
                >
                  REST Calls
                </button>
              </div>
              <span className="count-badge">{activeHistory.length} minutes</span>
            </div>
          </div>
          <div className="max-h-72 overflow-y-auto">
            <table>
              <thead className="sticky top-0 bg-slate-900">
                <tr>
                  <th>Timestamp</th>
                  {venues.map((v) => (
                    <th key={v} className="capitalize">{v} ({auditMode === "ws" ? "msg" : "calls"})</th>
                  ))}
                  <th>Total / min</th>
                </tr>
              </thead>
              <tbody>
                {[...activeHistory].reverse().map((h) => (
                  <tr key={h.minute}>
                    <td className="font-mono text-xs text-slate-400">
                      {new Date(h.timestamp).toLocaleTimeString()}
                    </td>
                    {venues.map((v) => (
                      <td key={v} className="font-mono text-slate-200 font-medium">
                        {h.counts[v] ?? 0}
                      </td>
                    ))}
                    <td className="font-mono font-bold text-cyan">
                      {h.total}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
