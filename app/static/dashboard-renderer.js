import { renderCharts } from './dashboard-charts.js';
import { renderDetail, renderRealtime } from './dashboard-core.js';
import {
  renderMarket,
  renderProviderInsights,
  renderRepricingEvents,
  renderTrades,
} from './dashboard-market.js';

export function renderDashboard(state, flags) {
  if (flags.realtime) {
    renderRealtime(state);
    renderMarket(state.market);
  }
  if (flags.detail) {
    renderDetail(state);
    renderProviderInsights(state.provider_insights);
    renderCharts(state.chart, state.provider_insights);
  }
  if (flags.activity) {
    renderTrades(state.recent_trades);
    renderRepricingEvents(state.recent_repricing_events);
  }
}
