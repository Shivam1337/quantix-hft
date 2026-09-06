import { fmt, setText, showToast } from './ui-utils.js';

let selectedMode = 'SIMULATION';
let hooks = {};

export function configureSettings(nextHooks) {
  hooks = { ...hooks, ...nextHooks };
}

export function renderTradingActivityControl(enabled) {
  const control = document.getElementById('global-trading-control');
  const input = document.getElementById('setting-trading-enabled');
  const label = document.getElementById('trading-activity-label');
  const description = document.getElementById('trading-activity-description');
  const alert = document.getElementById('trading-activity-alert');
  if (input) input.checked = enabled;
  control?.classList.toggle('paused', !enabled);
  setText(label, enabled ? 'Trading Activity Enabled' : 'Trading Activity Paused');
  setText(description, enabled ? 'New REAL and simulation entries may be submitted.' : 'New REAL and simulation entries are blocked. Existing live exposure remains risk-managed.');
  if (alert) {
    alert.className = `settings-alert ${enabled ? 'success' : 'paused'}`;
    setText(alert, enabled ? '✅ Global entry control is enabled for the selected execution mode.' : '🛑 Global pause is active. Market data stays online, but no new orders or paper trades can start.');
  }
}

export function selectTradingMode(mode, showWarning = true) {
  selectedMode = mode;
  const simulation = document.getElementById('mode-card-sim');
  const real = document.getElementById('mode-card-real');
  simulation?.classList.toggle('active', mode !== 'REAL');
  real?.classList.toggle('active', mode === 'REAL');
  if (mode === 'REAL' && showWarning) showToast('Selected REAL Mode: Click Save & Apply to activate live trading.', 'info');
}

export function renderSettingsView(data) {
  selectedMode = data.trading_mode || 'SIMULATION';
  selectTradingMode(selectedMode, false);
  renderTradingActivityControl(data.trading_enabled !== false);
  const values = {
    'setting-network': data.network || 'mainnet',
    'setting-acc-idx': data.account_index > 0 ? data.account_index : '',
    'setting-key-idx': data.api_key_index ?? 4,
    'setting-margin-pct': Math.round((data.trade_margin_fraction ?? 0.5) * 100),
    'setting-leverage': data.leverage ?? 50,
    'setting-min-lag': data.min_lag_trigger ?? 6,
    'setting-max-hold': data.max_hold_seconds ?? 12,
    'setting-sim-starting-balance': data.simulation_starting_balance ?? 100,
  };
  Object.entries(values).forEach(([id, value]) => {
    const input = document.getElementById(id);
    if (input) input.value = value;
  });
  const privateKey = document.getElementById('setting-api-privkey');
  if (privateKey) privateKey.value = '';
  hooks.onMinLagChange?.(values['setting-min-lag']);
  const alert = document.getElementById('mode-status-alert');
  if (alert) {
    alert.className = `settings-alert ${data.is_real_eligible ? 'success' : 'warning'}`;
    setText(alert, data.is_real_eligible ? `✅ Account ready for REAL mode on Lighter ${String(data.network).toUpperCase()}!` : `ℹ️ ${data.eligibility_message || 'Setup wallet and Lighter account to enable REAL trading.'}`);
  }
}

export async function loadSettingsData() {
  try {
    const response = await fetch('/api/settings', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderSettingsView(await response.json());
  } catch (error) {
    console.warn('Failed to load settings:', error);
    showToast('Failed to load settings', 'error');
  }
}

export async function toggleTradingActivity(enabled) {
  const input = document.getElementById('setting-trading-enabled');
  if (input) input.disabled = true;
  try {
    const response = await fetch('/api/settings/trading-activity', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to update global trading activity');
    renderSettingsView(data.settings);
    showToast(enabled ? 'Global trading activity enabled.' : 'Global trading activity paused. No new entries will be sent.', enabled ? 'success' : 'info');
  } catch (error) {
    renderTradingActivityControl(!enabled);
    showToast(`Unable to update global trading activity: ${error.message}`, 'error');
  } finally {
    if (input) input.disabled = false;
  }
}

function inputValue(id) {
  return document.getElementById(id)?.value || '';
}

export async function saveSystemSettings() {
  const button = document.getElementById('save-settings-btn');
  if (button) { button.disabled = true; button.innerText = '⌛ Saving...'; }
  const payload = {
    trading_mode: selectedMode,
    trading_enabled: document.getElementById('setting-trading-enabled')?.checked !== false,
    network: inputValue('setting-network'),
    account_index: inputValue('setting-acc-idx') ? parseInt(inputValue('setting-acc-idx'), 10) : null,
    api_key_index: parseInt(inputValue('setting-key-idx'), 10) || 4,
    trade_margin_fraction: parseFloat(inputValue('setting-margin-pct')) / 100,
    leverage: parseFloat(inputValue('setting-leverage')) || 50,
    min_lag_trigger: parseFloat(inputValue('setting-min-lag')) || 6,
    max_hold_seconds: parseFloat(inputValue('setting-max-hold')) || 12,
  };
  const balance = parseFloat(inputValue('setting-sim-starting-balance'));
  if (!Number.isNaN(balance)) payload.simulation_starting_balance = balance;
  const key = inputValue('setting-api-privkey').trim();
  if (key) payload.api_private_key = key;
  try {
    const response = await fetch('/api/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Failed to save settings');
    renderSettingsView(data);
    showToast(`Settings saved! ${data.trading_enabled === false ? 'Global trading is paused' : `Execution mode: ${data.trading_mode}`}`, 'success');
    return true;
  } catch (error) {
    showToast(`Error saving settings: ${error.message}`, 'error');
    return false;
  } finally {
    if (button) { button.disabled = false; button.innerText = '💾 Save & Apply'; }
  }
}

export async function confirmResetSimulation() {
  const balance = parseFloat(inputValue('setting-sim-starting-balance')) || 100;
  const message = `Are you sure you want to reset the simulation system?\n\n• All paper trades and history will be cleared.\n• Performance and PnL will be reset to 0.\n• Simulation account balance will be restarted at $${balance.toFixed(2)}.\n• A new simulation run will be started from time 0.`;
  if (!window.confirm(message)) return;
  setText('reset-sim-feedback', 'Resetting system...');
  try {
    if (!await saveSystemSettings()) throw new Error('Settings could not be saved');
    const response = await fetch('/api/system/reset-simulation', { method: 'POST' });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    showToast(`Simulation reset! Starting balance: $${fmt(data.starting_balance, 2)}`, 'success');
    setText('reset-sim-feedback', `✅ Reset complete at ${data.reset_at ? new Date(data.reset_at).toLocaleTimeString() : 'now'}`);
    hooks.onSimulationReset?.();
  } catch (error) {
    showToast(`Reset failed: ${error.message}`, 'error');
    setText('reset-sim-feedback', `❌ Error: ${error.message}`);
  }
}

export function exposeSettingsActions() {
  Object.assign(window, { confirmResetSimulation, saveSystemSettings, selectTradingMode, toggleTradingActivity });
}
