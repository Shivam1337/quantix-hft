import type { SystemMetrics } from "../types";
import type { RoutePath } from "../router";

type Props = {
  currentPath: RoutePath;
  onNavigate: (path: RoutePath) => void;
  metrics: SystemMetrics | null;
  metricsError?: string;
  opportunityCount: number;
  positionCount: number;
  isOpenMobile?: boolean;
  onCloseMobile?: () => void;
};

function getLoadColor(pct: number): string {
  if (pct >= 85) return "bg-rose-500 text-rose-400";
  if (pct >= 70) return "bg-amber-500 text-amber-400";
  return "bg-cyan text-cyan";
}

export function Sidebar(props: Props) {
  const m = props.metrics;
  const navItems: { path: RoutePath; label: string; count?: number; icon: string }[] = [
    { path: "/", label: "Dashboard", icon: "⊞" },
    { path: "/opportunities", label: "Opportunities", count: props.opportunityCount, icon: "⌖" },
    { path: "/positions", label: "Positions & Logs", count: props.positionCount, icon: "⚑" },
    { path: "/exchanges", label: "Exchanges & Coins", icon: "🏛" },
    { path: "/throughput", label: "Stream Traffic", icon: "📈" },
    { path: "/simulator", label: "Yield Simulator", icon: "⚡" },
    { path: "/settings", label: "Risk & Settings", icon: "⚙" },
  ];

  return (
    <>
      {/* Mobile backdrop */}
      {props.isOpenMobile && (
        <div
          className="fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden"
          onClick={props.onCloseMobile}
        />
      )}

      <aside
        className={`fixed bottom-0 left-0 top-0 z-40 flex w-64 flex-col border-r border-slate-800 bg-[#060c18] transition-transform duration-200 lg:translate-x-0 ${
          props.isOpenMobile ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        }`}
      >
        {/* Brand Header */}
        <div className="flex h-16 items-center justify-between border-b border-slate-800/80 px-5">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-cyan/40 bg-cyan/10 font-bold text-cyan">
              Q
            </div>
            <div>
              <div className="text-sm font-semibold tracking-wide text-white">QUANTIX HFT</div>
              <div className="text-[10px] uppercase tracking-wider text-slate-400">Delta Neutral</div>
            </div>
          </div>
          {props.onCloseMobile && (
            <button
              onClick={props.onCloseMobile}
              className="text-slate-400 hover:text-white lg:hidden"
              aria-label="Close menu"
            >
              ✕
            </button>
          )}
        </div>

        {/* Navigation Menu */}
        <div className="px-3 py-4">
          <p className="px-3 pb-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">
            Pages
          </p>
          <nav className="space-y-1">
            {navItems.map((item) => {
              const active = props.currentPath === item.path;
              return (
                <a
                  key={item.path}
                  href={item.path}
                  onClick={(e) => {
                    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                    e.preventDefault();
                    props.onNavigate(item.path);
                    props.onCloseMobile?.();
                  }}
                  className={`group flex w-full items-center justify-between rounded-lg px-3 py-2 text-xs font-medium transition ${
                    active
                      ? "border border-cyan/30 bg-cyan/10 text-cyan"
                      : "text-slate-400 hover:bg-slate-900/80 hover:text-slate-200"
                  }`}
                >
                  <div className="flex items-center gap-2.5">
                    <span className={`text-sm ${active ? "text-cyan" : "text-slate-500 group-hover:text-slate-300"}`}>
                      {item.icon}
                    </span>
                    <span>{item.label}</span>
                  </div>
                  {item.count !== undefined && (
                    <span
                      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                        active ? "bg-cyan/20 text-cyan" : "bg-slate-800 text-slate-400"
                      }`}
                    >
                      {item.count}
                    </span>
                  )}
                </a>
              );
            })}
          </nav>
        </div>

        {/* System Load & Diagnostics Monitor */}
        <div className="mt-auto border-t border-slate-800/80 p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">
              System Load
            </p>
            <span className="flex items-center gap-1.5 text-[10px] text-emerald-400">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" />
              Live
            </span>
          </div>

          {props.metricsError ? (
            <div className="rounded border border-amber-500/20 bg-amber-500/10 p-2 text-[11px] text-amber-300">
              Telemetry unavailable
            </div>
          ) : !m ? (
            <div className="animate-pulse space-y-2 text-[11px] text-slate-500">
              <div className="h-3 rounded bg-slate-800" />
              <div className="h-3 rounded bg-slate-800" />
            </div>
          ) : (
            <div className="space-y-2.5 text-[11px]">
              {/* Host CPU */}
              <div>
                <div className="flex justify-between text-slate-400">
                  <span>Host CPU</span>
                  <span className="font-mono text-slate-200">{m.system_cpu_pct}%</span>
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                  <div
                    className={`h-full transition-all duration-300 ${getLoadColor(m.system_cpu_pct).split(" ")[0]}`}
                    style={{ width: `${Math.min(m.system_cpu_pct, 100)}%` }}
                  />
                </div>
              </div>

              {/* Host RAM */}
              <div>
                <div className="flex justify-between text-slate-400">
                  <span>Host RAM</span>
                  <span className="font-mono text-slate-200">
                    {m.system_ram_pct}% <span className="text-[9px] text-slate-500">({m.system_ram_used_gb}G)</span>
                  </span>
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                  <div
                    className={`h-full transition-all duration-300 ${getLoadColor(m.system_ram_pct).split(" ")[0]}`}
                    style={{ width: `${Math.min(m.system_ram_pct, 100)}%` }}
                  />
                </div>
              </div>

              {/* Host Disk */}
              <div>
                <div className="flex justify-between text-slate-400">
                  <span>Host Disk</span>
                  <span className="font-mono text-slate-200">
                    {m.system_disk_pct}% <span className="text-[9px] text-slate-500">({m.system_disk_used_gb}G)</span>
                  </span>
                </div>
                <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-slate-800">
                  <div
                    className={`h-full transition-all duration-300 ${getLoadColor(m.system_disk_pct).split(" ")[0]}`}
                    style={{ width: `${Math.min(m.system_disk_pct, 100)}%` }}
                  />
                </div>
              </div>

              {/* Process CPU & RAM */}
              <div className="mt-2 grid grid-cols-2 gap-1.5 rounded-md border border-slate-800/80 bg-slate-900/40 p-2">
                <div>
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">App CPU</div>
                  <div className="font-mono text-xs font-semibold text-slate-200">{m.process_cpu_pct}%</div>
                </div>
                <div>
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">App RAM</div>
                  <div className="font-mono text-xs font-semibold text-slate-200">{m.process_ram_mb} MB</div>
                </div>
              </div>

              {/* Postgres & Redis Size */}
              <div className="grid grid-cols-2 gap-1.5 rounded-md border border-slate-800/80 bg-slate-900/40 p-2">
                <div>
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">Postgres</div>
                  <div className="truncate font-mono text-xs font-semibold text-slate-200" title={m.postgres_size_human}>
                    {m.postgres_size_human}
                  </div>
                </div>
                <div>
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">Redis</div>
                  <div className="truncate font-mono text-xs font-semibold text-slate-200" title={m.redis_size_human}>
                    {m.redis_size_human}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
