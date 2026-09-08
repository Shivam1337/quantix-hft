import { CoinDetailsPage } from "../pages/CoinDetailsPage";
import { DashboardPage } from "../pages/DashboardPage";
import { ExchangesPage } from "../pages/ExchangesPage";
import { OpportunitiesPage } from "../pages/OpportunitiesPage";
import { PositionsPage } from "../pages/PositionsPage";
import { SettingsPage } from "../pages/SettingsPage";
import { SimulatorPage } from "../pages/SimulatorPage";
import { ThroughputPage } from "../pages/ThroughputPage";
import type { RoutePath } from "../router";
import type {
  Opportunity,
  Position,
  Settings,
  Simulation,
  SimulationAccount,
  SystemMetrics,
  ThemeMode,
  TradeLog,
} from "../types";

type Props = {
  currentPath: RoutePath;
  queryParams?: URLSearchParams;
  onNavigate?: (to: string) => void;
  metrics?: SystemMetrics | null;
  metricsError?: string;
  opportunities: Opportunity[];
  positions: Position[];
  logs: TradeLog[];
  settings: Settings | null;
  selected: Opportunity | null;
  simulation: Simulation | null;
  account?: SimulationAccount | null;
  capital: string;
  minApr: string;
  pair: string;
  theme: ThemeMode;
  onThemeChange: (mode: ThemeMode) => void;
  onCapitalChange: (val: string) => void;
  onMinAprChange: (val: string) => void;
  onPairChange: (val: string) => void;
  onSelectSimulate: (item: Opportunity) => void;
  onSelectOpportunity: (item: Opportunity | null) => void;
  onRunSimulation: () => void;
  onResetSimulation?: () => void;
  onSaveSettings: (val: Partial<Settings>) => Promise<void>;
};

export function PageView(p: Props) {
  switch (p.currentPath) {
    case "/opportunities":
      return (
        <OpportunitiesPage
          opportunities={p.opportunities}
          capital={p.capital}
          minApr={p.minApr}
          pair={p.pair}
          onCapitalChange={p.onCapitalChange}
          onMinAprChange={p.onMinAprChange}
          onPairChange={p.onPairChange}
          onSelectSimulate={p.onSelectSimulate}
        />
      );
    case "/positions":
      return (
        <PositionsPage
          positions={p.positions}
          logs={p.logs}
          account={p.account}
          onResetSimulation={p.onResetSimulation}
        />
      );
    case "/exchanges":
      return (
        <ExchangesPage
          onSelectCoin={(sym, venue) =>
            p.onNavigate?.(`/exchanges/coin?venue=${encodeURIComponent(venue)}&symbol=${encodeURIComponent(sym)}`)
          }
        />
      );
    case "/exchanges/coin":
      return (
        <CoinDetailsPage
          venue={p.queryParams?.get("venue") ?? "hyperliquid"}
          symbol={p.queryParams?.get("symbol") ?? "BTC-PERP"}
          onBack={() => p.onNavigate?.("/exchanges")}
          onNavigate={(to) => p.onNavigate?.(to)}
          onSelectSimulate={(item) => p.onSelectSimulate?.(item)}
        />
      );
    case "/throughput":
      return <ThroughputPage />;
    case "/simulator":
      return (
        <SimulatorPage
          opportunities={p.opportunities}
          selected={p.selected}
          capital={p.capital}
          simulation={p.simulation}
          account={p.account ?? null}
          onSelectOpportunity={p.onSelectOpportunity}
          onRunSimulation={p.onRunSimulation}
          onResetSimulation={p.onResetSimulation ?? (() => {})}
        />
      );
    case "/settings":
      return (
        <SettingsPage
          settings={p.settings}
          theme={p.theme}
          onThemeChange={p.onThemeChange}
          onSaveSettings={p.onSaveSettings}
        />
      );
    case "/":
    default:
      return (
        <DashboardPage
          opportunities={p.opportunities}
          positions={p.positions}
          settings={p.settings}
          metrics={p.metrics ?? null}
          metricsError={p.metricsError}
          onNavigate={(to) => p.onNavigate?.(to)}
        />
      );
  }
}
