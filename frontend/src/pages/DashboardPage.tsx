import type { Opportunity, Position, Settings, SystemMetrics } from "../types";

type Props = {
  opportunities: Opportunity[];
  positions: Position[];
  settings: Settings | null;
  metrics: SystemMetrics | null;
  metricsError?: string;
  onNavigate: (to: string) => void;
};

const VENUES = [
  { name: "Hyperliquid", key: "hyperliquid", interval: "1h", ws: "Connected" },
  { name: "Aevo", key: "aevo", interval: "1h", ws: "Connected" },
  { name: "Lighter", key: "lighter", interval: "1h", ws: "Connected" },
];

export function DashboardPage(p: Props) {
  const topOpp = p.opportunities[0];
  const totalPnl = p.positions.reduce(
    (s, i) => s + (i.net_pnl_usd ?? i.funding_pnl_usd + i.basis_pnl_usd - i.fees_usd),
    0
  );
  const totalCapital = p.positions.reduce((s, i) => s + i.size_usd, 0);

  return (
    <div className="space-y-6">
      <section className="card p-5">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800/80 pb-4">
          <div className="flex items-center gap-3">
            <span className="relative flex h-3 w-3">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-3 w-3 rounded-full bg-emerald-500" />
            </span>
            <div>
              <h2 className="text-base font-semibold text-white">Delta-Neutral Execution Engine</h2>
              <p className="text-xs text-slate-400">Mode: Live-Read-Only / Paper Execution · Auto-Unwind: {p.settings?.auto_unwind ? "Active" : "Off"}</p>
            </div>
          </div>
          <div className="flex items-center gap-4 text-xs">
            <span className="rounded bg-slate-800 px-2.5 py-1 text-slate-300">
              PostgreSQL: <strong className="text-cyan">{p.metrics?.postgres_size_human ?? "Active"}</strong>
            </span>
            <span className="rounded bg-slate-800 px-2.5 py-1 text-slate-300">
              Redis Cache: <strong className="text-cyan">{p.metrics?.redis_size_human ?? "Active"}</strong>
            </span>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
          {VENUES.map((v) => (
            <div key={v.key} className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 p-3">
              <div>
                <p className="text-xs font-medium text-white">{v.name}</p>
                <p className="text-[11px] text-slate-500">Interval: {v.interval} funding</p>
              </div>
              <span className="flex items-center gap-1.5 text-xs text-emerald-400">
                <i className="status-dot" /> {v.ws}
              </span>
            </div>
          ))}
        </div>
      </section>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <section className="card p-5">
          <p className="eyebrow text-cyan">Market & Portfolio Pulse</p>
          <div className="mt-4 grid grid-cols-2 gap-4">
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-3.5">
              <p className="text-xs text-slate-400">Best Net APR</p>
              <p className="mt-1 text-2xl font-bold text-emerald-400">
                {topOpp ? `${topOpp.net_apr_pct.toFixed(1)}%` : "0.0%"}
              </p>
              <p className="mt-0.5 truncate text-[11px] text-slate-500">
                {topOpp ? `${topOpp.symbol} (${topOpp.long_venue}/${topOpp.short_venue})` : "Scanning feeds..."}
              </p>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-3.5">
              <p className="text-xs text-slate-400">Active Spreads</p>
              <p className="mt-1 text-2xl font-bold text-white">{p.opportunities.length}</p>
              <p className="mt-0.5 text-[11px] text-slate-500">Pairs above APR floor</p>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-3.5">
              <p className="text-xs text-slate-400">Paper Capital</p>
              <p className="mt-1 text-2xl font-bold text-white">${totalCapital.toLocaleString()}</p>
              <p className="mt-0.5 text-[11px] text-slate-500">{p.positions.length} active positions</p>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-3.5">
              <p className="text-xs text-slate-400">Portfolio Net PnL</p>
              <p className={`mt-1 text-2xl font-bold ${totalPnl >= 0 ? "text-cyan" : "text-amber-400"}`}>
                ${totalPnl.toFixed(2)}
              </p>
              <p className="mt-0.5 text-[11px] text-slate-500">After paid fees</p>
            </div>
          </div>
        </section>

        <section className="card p-5">
          <p className="eyebrow text-cyan">System & Process Telemetry</p>
          {p.metricsError && <p className="mt-2 text-xs text-amber-400">{p.metricsError}</p>}
          <div className="mt-4 grid grid-cols-2 gap-4">
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-3.5">
              <div className="flex justify-between text-xs text-slate-400">
                <span>System CPU</span>
                <span className="text-white font-medium">{p.metrics?.system_cpu_pct ?? 0}%</span>
              </div>
              <div className="mt-2 h-1.5 w-full rounded-full bg-slate-800">
                <div className="h-1.5 rounded-full bg-cyan" style={{ width: `${Math.min(100, p.metrics?.system_cpu_pct ?? 0)}%` }} />
              </div>
              <p className="mt-2 text-[11px] text-slate-500">Process: {p.metrics?.process_cpu_pct ?? 0}% CPU</p>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-3.5">
              <div className="flex justify-between text-xs text-slate-400">
                <span>System Memory</span>
                <span className="text-white font-medium">{p.metrics?.system_ram_pct ?? 0}%</span>
              </div>
              <div className="mt-2 h-1.5 w-full rounded-full bg-slate-800">
                <div className="h-1.5 rounded-full bg-emerald-400" style={{ width: `${Math.min(100, p.metrics?.system_ram_pct ?? 0)}%` }} />
              </div>
              <p className="mt-2 text-[11px] text-slate-500">RAM: {p.metrics?.system_ram_used_gb ?? 0} / {p.metrics?.system_ram_total_gb ?? 0} GB</p>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-3.5">
              <div className="flex justify-between text-xs text-slate-400">
                <span>Disk Space</span>
                <span className="text-white font-medium">{p.metrics?.system_disk_pct ?? 0}%</span>
              </div>
              <div className="mt-2 h-1.5 w-full rounded-full bg-slate-800">
                <div className="h-1.5 rounded-full bg-indigo-400" style={{ width: `${Math.min(100, p.metrics?.system_disk_pct ?? 0)}%` }} />
              </div>
              <p className="mt-2 text-[11px] text-slate-500">Used: {p.metrics?.system_disk_used_gb ?? 0} / {p.metrics?.system_disk_total_gb ?? 0} GB</p>
            </div>
            <div className="rounded-lg border border-slate-800/80 bg-slate-900/40 p-3.5">
              <p className="text-xs text-slate-400">API Process Footprint</p>
              <p className="mt-1 text-2xl font-bold text-white">{p.metrics?.process_ram_mb ?? 0} <span className="text-xs font-normal text-slate-400">MB</span></p>
              <p className="mt-0.5 text-[11px] text-slate-500">{p.metrics?.process_ram_pct ?? 0}% of system RAM</p>
            </div>
          </div>
        </section>
      </div>

      <section>
        <div className="mb-3 flex items-center justify-between">
          <p className="eyebrow text-cyan">Dedicated Platform Modules</p>
          <span className="text-xs text-slate-500">Select any module to open its dedicated page</span>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <button onClick={() => p.onNavigate("/opportunities")} className="card p-4 text-left transition-all hover:border-cyan/50 hover:bg-slate-900/60">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-white">Opportunity Scanner</span>
              <span className="text-xs text-cyan">/opportunities →</span>
            </div>
            <p className="mt-2 text-xs text-slate-400">Cross-exchange matrix with delta-neutral APR ranking, basis threshold filters, and one-click execution.</p>
            <div className="mt-3 text-[11px] text-slate-500 font-medium">Status: {p.opportunities.length} opportunities detected</div>
          </button>

          <button onClick={() => p.onNavigate("/positions")} className="card p-4 text-left transition-all hover:border-cyan/50 hover:bg-slate-900/60">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-white">Positions & Audit</span>
              <span className="text-xs text-cyan">/positions →</span>
            </div>
            <p className="mt-2 text-xs text-slate-400">Live paper positions, delta-neutral basis tracker, funding PnL collection, and database execution audit log.</p>
            <div className="mt-3 text-[11px] text-slate-500 font-medium">{p.positions.length} active positions · ${totalPnl.toFixed(2)} PnL</div>
          </button>

          <button onClick={() => p.onNavigate("/exchanges")} className="card p-4 text-left transition-all hover:border-cyan/50 hover:bg-slate-900/60">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-white">Exchange Directory</span>
              <span className="text-xs text-cyan">/exchanges →</span>
            </div>
            <p className="mt-2 text-xs text-slate-400">Configured venues directory and 30 perp instruments with deep-links to dedicated coin analytics pages.</p>
            <div className="mt-3 text-[11px] text-slate-500 font-medium">Venues: Hyperliquid, Aevo, Lighter</div>
          </button>

          <button onClick={() => p.onNavigate("/throughput")} className="card p-4 text-left transition-all hover:border-cyan/50 hover:bg-slate-900/60">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-white">WebSocket Ingestion</span>
              <span className="text-xs text-cyan">/throughput →</span>
            </div>
            <p className="mt-2 text-xs text-slate-400">Minute-by-minute streaming telemetry tracking incoming message throughput across each exchange feed.</p>
            <div className="mt-3 text-[11px] text-slate-500 font-medium">Real-time telemetry stream active</div>
          </button>

          <button onClick={() => p.onNavigate("/simulator")} className="card p-4 text-left transition-all hover:border-cyan/50 hover:bg-slate-900/60">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-white">Yield Simulator</span>
              <span className="text-xs text-cyan">/simulator →</span>
            </div>
            <p className="mt-2 text-xs text-slate-400">Simulate delta-neutral carry yield, round-trip fee drag, breakeven holding periods, and net dollar return.</p>
            <div className="mt-3 text-[11px] text-slate-500 font-medium">Custom capital & holding horizon simulator</div>
          </button>

          <button onClick={() => p.onNavigate("/settings")} className="card p-4 text-left transition-all hover:border-cyan/50 hover:bg-slate-900/60">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-white">System Configuration</span>
              <span className="text-xs text-cyan">/settings →</span>
            </div>
            <p className="mt-2 text-xs text-slate-400">Risk parameters, minimum APR threshold floor, open interest limits, webhook alerts, and theme mode.</p>
            <div className="mt-3 text-[11px] text-slate-500 font-medium">Min APR: {p.settings?.min_apr ?? 10}% floor</div>
          </button>
        </div>
      </section>
    </div>
  );
}
