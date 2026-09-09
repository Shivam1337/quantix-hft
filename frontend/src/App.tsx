import { useEffect, useMemo, useState } from "react";
import {
  connectMarketSocket, getLogs, getOpportunities,
  getPositions, getSettings, getSimulationAccount, getSystemMetrics,
  resetSimulation, simulate, updateSettings,
} from "./api";
import { MetricCard } from "./components/MetricCard";
import { PageView } from "./components/PageView";
import { Sidebar } from "./components/Sidebar";
import { useRouter, type RoutePath } from "./router";
import { useTheme } from "./useTheme";
import type { Opportunity, Position, Settings, Simulation, SimulationAccount, SystemMetrics, TradeLog } from "./types";

function App() {
  const { theme, setTheme } = useTheme();
  const { currentPath, navigate, queryParams } = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [logs, setLogs] = useState<TradeLog[]>([]);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [selected, setSelected] = useState<Opportunity | null>(null);
  const [simulation, setSimulation] = useState<Simulation | null>(null);
  const [account, setAccount] = useState<SimulationAccount | null>(null);
  const [metrics, setMetrics] = useState<SystemMetrics | null>(null);
  const [metricsError, setMetricsError] = useState("");
  const [capital, setCapital] = useState("1000");
  const [minApr, setMinApr] = useState("0");
  const [pair, setPair] = useState("");
  const [error, setError] = useState("");

  const loadData = async () => {
    try {
      const [opps, pos, sett, acc] = await Promise.all([
        getOpportunities("min_apr=0"),
        getPositions(),
        getSettings(),
        getSimulationAccount().catch(() => null),
      ]);
      if (opps && opps.length > 0) {
        setOpportunities(opps);
      }
      setPositions(pos);
      setSettings(sett);
      if (acc) setAccount(acc);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "API unavailable");
    }
  };

  const loadMetrics = async () => {
    try {
      setMetrics(await getSystemMetrics());
      setMetricsError("");
    } catch (err) {
      setMetricsError(err instanceof Error ? err.message : "Telemetry unavailable");
    }
  };

  const loadLogs = async () => {
    try {
      setLogs(await getLogs());
    } catch {}
  };

  useEffect(() => {
    void loadData();
    const dataTimer = window.setInterval(() => void loadData(), 15000);
    const socket = connectMarketSocket((data) => {
      if (data.opportunities && data.opportunities.length > 0) {
        setOpportunities(data.opportunities);
      }
      if (data.positions) {
        setPositions(data.positions.filter((x) => x.status === "open"));
      }
      if (data.account) {
        setAccount(data.account);
      }
    });
    return () => {
      window.clearInterval(dataTimer);
      socket.close();
    };
  }, []);

  useEffect(() => {
    void loadMetrics();
    const timer = window.setInterval(() => void loadMetrics(), 5000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (currentPath === "/positions") void loadLogs();
  }, [currentPath]);

  const filteredOpportunities = useMemo(() => {
    const minAprNum = Number(minApr) || 0;
    return opportunities.filter((item) => {
      if (item.net_apr_pct < minAprNum) return false;
      if (pair) {
        const [v1, v2] = pair.split("/");
        if (v1 && v2) {
          const venues = new Set([item.long_venue.toLowerCase(), item.short_venue.toLowerCase()]);
          if (!venues.has(v1.toLowerCase()) || !venues.has(v2.toLowerCase())) return false;
        }
      }
      return true;
    });
  }, [opportunities, minApr, pair]);

  const topApr = useMemo(
    () => filteredOpportunities[0]?.net_apr_pct ?? opportunities[0]?.net_apr_pct ?? 0,
    [filteredOpportunities, opportunities]
  );
  const totalPnl = positions.reduce(
    (sum, item) => sum + (item.funding_pnl_usd || 0) + (item.basis_pnl_usd || 0),
    0
  );

  const resetSim = async () => {
    try {
      const res = await resetSimulation();
      setAccount(res.account);
      setPositions([]);
      setLogs([]);
      await loadData();
      if (currentPath === "/positions") await loadLogs();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not reset simulation");
    }
  };

  const runSimulation = async () => {
    if (!selected) return;
    try { setSimulation(await simulate(selected.id, Number(capital), 30)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not simulate"); }
  };

  const saveSettings = async (values: Partial<Settings>) => {
    try { setSettings(await updateSettings(values)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save settings"); }
  };

  const titles: Record<RoutePath, { tag: string; title: React.ReactNode }> = {
    "/": { tag: "system health & command center", title: <>System <span>Overview</span></> },
    "/opportunities": { tag: "cross-exchange matrix", title: <>Opportunity <span>Scanner</span></> },
    "/positions": { tag: "portfolio & risk", title: <>Open Positions & <span>Audit</span></> },
    "/exchanges": { tag: "venues & configured instruments", title: <>Exchange <span>Directory</span></> },
    "/exchanges/coin": { tag: "coin carry & history", title: <>Coin <span>Analytics</span></> },
    "/throughput": { tag: "websocket message ingestion", title: <>Stream <span>Throughput Telemetry</span></> },
    "/simulator": { tag: "carry & yield calculation", title: <>Yield <span>Simulator</span></> },
    "/settings": { tag: "parameters & guardrails", title: <>System <span>Configuration</span></> },
  };

  return (
    <div className="min-h-screen bg-[#08111f]">
      <Sidebar
        currentPath={currentPath}
        onNavigate={navigate}
        metrics={metrics}
        metricsError={metricsError}
        opportunityCount={filteredOpportunities.length}
        positionCount={positions.length}
        isOpenMobile={mobileOpen}
        onCloseMobile={() => setMobileOpen(false)}
      />

      <div className="flex flex-col lg:pl-64">
        <header className="shell flex items-center justify-between border-b border-slate-800/80 py-5">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setMobileOpen(true)}
              className="rounded-lg border border-slate-700 bg-slate-900/60 p-2 text-slate-300 hover:text-white lg:hidden"
              aria-label="Open sidebar"
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
              </svg>
            </button>
            <div>
              <p className="eyebrow text-cyan">{titles[currentPath]?.tag ?? "Navigation"}</p>
              <h1 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
                {titles[currentPath]?.title ?? <>Quantix <span>HFT</span></>}
              </h1>
            </div>
          </div>
          <div className="hidden text-right md:block">
            <p className="eyebrow">Feed status</p>
            <p className="mt-1 flex items-center justify-end gap-2 text-xs text-emerald-300">
              <i className="status-dot" /> Live streams active
            </p>
          </div>
        </header>

        <main className="shell flex-1 py-6">
          {error && <div className="alert mb-5">{error}</div>}

          {(currentPath === "/opportunities" || currentPath === "/positions") && (
            <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
              <MetricCard label="Best net APR" value={`${topApr.toFixed(1)}%`} detail="after 30d fee drag" />
              <MetricCard
                label="Account balance"
                value={`$${(account?.current_balance ?? 1000).toLocaleString(undefined, { minimumFractionDigits: 2 })}`}
                detail={`Initial: $${(account?.initial_balance ?? 1000).toLocaleString()} · $${((account?.initial_balance ?? 1000) / 2).toLocaleString()} margin/leg · ${account?.leverage ?? 3}x`}
                accent="white"
              />
              <MetricCard label="Portfolio PnL" value={`$${totalPnl.toFixed(2)}`} detail="funding + basis" accent={totalPnl >= 0 ? "cyan" : "amber"} />
            </div>
          )}

          <PageView
            currentPath={currentPath}
            queryParams={queryParams}
            onNavigate={navigate}
            metrics={metrics}
            metricsError={metricsError}
            opportunities={filteredOpportunities}
            positions={positions}
            logs={logs}
            settings={settings}
            selected={selected}
            simulation={simulation}
            account={account}
            capital={capital}
            minApr={minApr}
            pair={pair}
            theme={theme}
            onThemeChange={setTheme}
            onCapitalChange={setCapital}
            onMinAprChange={setMinApr}
            onPairChange={setPair}
            onSelectSimulate={(item) => { setSelected(item); navigate("/simulator"); }}
            onSelectOpportunity={setSelected}
            onRunSimulation={runSimulation}
            onResetSimulation={resetSim}
            onSaveSettings={saveSettings}
          />
        </main>
      </div>
    </div>
  );
}

export default App;
