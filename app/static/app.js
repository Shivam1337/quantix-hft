import { initialiseApiInspector, exposeInspectorActions } from './api-inspector.js';
import { clearCharts, initialiseCharts, updateLagChartThresholds } from './dashboard-charts.js';
import { setDashboardActive, startDashboard } from './dashboard-stream.js';
import { resetActivityTables } from './dashboard-market.js';
import { exposeDatabaseActions } from './database-status.js';
import { initialiseNavigation } from './navigation.js';
import { configureSettings, exposeSettingsActions, loadSettingsData } from './settings-controller.js';
import { exposeUtilityActions } from './ui-utils.js';
import { exposeWalletActions, loadWalletData } from './wallet-controller.js';

function resetSimulationView() {
  clearCharts();
  resetActivityTables();
}

exposeUtilityActions();
exposeDatabaseActions();
exposeInspectorActions();
exposeWalletActions();
exposeSettingsActions();
initialiseCharts();
configureSettings({ onMinLagChange: updateLagChartThresholds, onSimulationReset: resetSimulationView });
initialiseNavigation({
  onApi: initialiseApiInspector,
  onDashboard: setDashboardActive,
  onSettings: loadSettingsData,
  onWallet: loadWalletData,
});
startDashboard();
