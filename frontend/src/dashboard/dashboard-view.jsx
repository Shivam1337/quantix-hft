import { DashboardCharts } from './chart-panels.jsx';
import { DashboardCockpit } from './cockpit.jsx';
import { DashboardHeader } from './dashboard-header.jsx';
import { DashboardTables } from './dashboard-tables.jsx';
import { PerformanceStrip } from './performance-strip.jsx';
import { TickerStrip } from './ticker-strip.jsx';
import { useDashboardState } from './store.js';

const EMPTY = [];

export function DashboardView({ minLag }) {
  const state = useDashboardState();
  return (
    <section id="view-dashboard" class="view-pane active">
      <div class="app-container">
        <DashboardHeader state={state} />
        <TickerStrip market={state.market} />
        <PerformanceStrip performance={state.trading_performance} market={state.market} />
        <div class="main-divided-grid">
          <DashboardCockpit state={state} />
          <DashboardCharts chart={state.chart} providers={state.provider_insights} minLag={minLag} />
        </div>
        <DashboardTables
          trades={state.recent_trades || EMPTY}
          providers={state.provider_insights?.providers || EMPTY}
          comparisons={state.execution_comparisons || EMPTY}
          events={state.recent_repricing_events || EMPTY}
        />
      </div>
    </section>
  );
}
