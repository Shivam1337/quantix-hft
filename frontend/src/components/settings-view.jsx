import { useEffect, useState } from 'preact/hooks';
import { jsonHeaders, requestJson } from '../lib/http.js';
import { useToast } from '../ui/toasts.jsx';

const DEFAULTS = {
  trading_mode: 'SIMULATION', trading_enabled: true, network: 'mainnet', account_index: '',
  api_key_index: 4, api_private_key: '', trade_margin_fraction: 0.5, leverage: 50,
  min_lag_trigger: 6, max_hold_seconds: 12, simulation_starting_balance: 100,
};

const MODES = [
  ['SIMULATION', 'mode-card-sim', '🟡 SIMULATION (Paper Trading)', 'Zero risk. Executes simulated orders across live Lighter bid/ask spread with a virtual balance.'],
  ['REAL', 'mode-card-real', '🟢 REAL (Live Lighter Execution)', 'Active capital. Signs and submits real IOC orders to zkLighter. Requires a funded account and API key.'],
  ['DUAL', 'mode-card-dual', '🟣 DUAL (Live + Matched Simulation)', 'Submits the configured real IOC order and records a same-signal L2 simulated fill for comparison.'],
];

function fieldValue(settings, key) {
  const value = settings[key];
  return value == null ? '' : value;
}

function numberOr(value, fallback) {
  if (value === '' || value == null) return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function SettingsView({ onSettingsChange }) {
  const toast = useToast();
  const [settings, setSettings] = useState(DEFAULTS);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showApiPrivateKey, setShowApiPrivateKey] = useState(false);

  function accept(data) {
    const next = { ...DEFAULTS, ...data, api_private_key: '' };
    setSettings(next);
    setShowApiPrivateKey(false);
    onSettingsChange?.(next);
    return next;
  }

  useEffect(() => {
    requestJson('/api/settings').then((data) => {
      accept(data);
      setLoaded(true);
    }).catch((error) => toast(`Failed to load settings: ${error.message}`, 'error'));
  }, []);

  function update(key, value) {
    setSettings((current) => ({ ...current, [key]: value }));
  }

  async function save(announce = true) {
    setSaving(true);
    const payload = {
      trading_mode: settings.trading_mode,
      trading_enabled: settings.trading_enabled !== false,
      network: settings.network,
      account_index: settings.account_index === '' ? null : numberOr(settings.account_index, null),
      api_key_index: numberOr(settings.api_key_index, 4),
      trade_margin_fraction: numberOr(settings.trade_margin_fraction, 0.5),
      leverage: numberOr(settings.leverage, 50),
      min_lag_trigger: numberOr(settings.min_lag_trigger, 6),
      max_hold_seconds: numberOr(settings.max_hold_seconds, 12),
      simulation_starting_balance: numberOr(settings.simulation_starting_balance, 100),
    };
    if (settings.api_private_key.trim()) payload.api_private_key = settings.api_private_key.trim();
    try {
      const data = await requestJson('/api/settings', {
        method: 'POST', headers: jsonHeaders(), body: JSON.stringify(payload),
      });
      accept(data);
      if (announce) toast(`Settings saved. Execution mode: ${data.trading_mode}.`, 'success');
      return data;
    } catch (error) {
      toast(`Unable to save settings: ${error.message}`, 'error');
      return null;
    } finally {
      setSaving(false);
    }
  }

  async function toggleTrading(enabled) {
    setSaving(true);
    try {
      const data = await requestJson('/api/settings/trading-activity', {
        method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ enabled }),
      });
      accept(data.settings);
      toast(enabled ? 'Global trading activity enabled.' : 'Global trading activity paused.', enabled ? 'success' : 'info');
    } catch (error) {
      toast(`Unable to update trading activity: ${error.message}`, 'error');
    } finally {
      setSaving(false);
    }
  }

  async function resetSimulation() {
    const balance = numberOr(settings.simulation_starting_balance, 100);
    const confirmed = window.confirm(`Reset all paper trades and DUAL comparison history to a $${balance.toFixed(2)} balance? This is blocked while a live Lighter order or position is active.`);
    if (!confirmed || !(await save(false))) return;
    try {
      const data = await requestJson('/api/system/reset-simulation', { method: 'POST' });
      toast(`Simulation reset. Starting balance: $${Number(data.starting_balance).toFixed(2)}.`, 'success');
    } catch (error) {
      toast(`Reset failed: ${error.message}`, 'error');
    }
  }

  const enabled = settings.trading_enabled !== false;
  const eligible = settings.is_real_eligible === true;
  return (
    <section id="view-settings" class="view-pane">
      <div class="view-content-wrapper">
        <div class="view-header"><div><div class="view-title">⚙️ System Configuration &amp; Execution Settings</div><div class="view-subtitle">Configure trading modes, Lighter API keys, and risk management thresholds.</div></div><div class="view-actions"><button class="btn btn-primary" disabled={saving || !loaded} onClick={() => save()}>{saving ? '⌛ Saving...' : '💾 Save & Apply'}</button></div></div>
        <div class={`settings-section global-trading-control ${enabled ? '' : 'paused'}`}>
          <div class="settings-section-title">🛑 Global Trading Control</div>
          <div class="global-trading-row"><div><div class="global-trading-label">{enabled ? 'Trading Activity Enabled' : 'Trading Activity Paused'}</div><div class="global-trading-description">{enabled ? 'New simulation, REAL, and paired DUAL entries may be submitted.' : 'Market data stays online, but no new orders or paper trades can start.'}</div></div><label class="toggle-switch" title="Pause or resume new trading entries"><input type="checkbox" checked={enabled} disabled={saving} onChange={(event) => toggleTrading(event.currentTarget.checked)} aria-label="Enable or pause global trading activity" /><span class="toggle-slider" /></label></div>
          <div class={`settings-alert ${enabled ? 'success' : 'paused'}`}>{enabled ? '✅ Global entry control is enabled for the selected execution mode.' : '🛑 Global pause is active. Existing live exposure remains risk-managed.'}</div>
        </div>
        <div class="settings-section">
          <div class="settings-section-title">Execution Mode Switcher</div>
          <div class="mode-selector-grid">{MODES.map(([mode, id, name, description]) => <button id={id} type="button" class={`mode-card ${settings.trading_mode === mode ? 'active' : ''}`} onClick={() => { update('trading_mode', mode); if (mode !== 'SIMULATION') toast(`Selected ${mode} mode. Click Save & Apply to activate it.`, 'info'); }} key={mode}><div class="mode-card-radio"><span class="radio-dot" /></div><div><div class="mode-card-name">{name}</div><div class="mode-card-desc">{description}</div></div></button>)}</div>
          <div class={`settings-alert ${eligible ? 'success' : 'warning'}`}>{eligible ? `✅ Account ready for REAL or DUAL mode on Lighter ${String(settings.network).toUpperCase()}!` : `ℹ️ ${settings.eligibility_message || 'Set up a wallet and Lighter account to enable REAL or DUAL trading.'}`}</div>
        </div>
        <div class="settings-section"><div class="settings-section-title">Lighter.xyz Credentials &amp; Network</div><div class="settings-form-grid">
          <label class="form-group"><span class="form-label">Network</span><select class="form-input" value={fieldValue(settings, 'network')} onChange={(event) => update('network', event.currentTarget.value)}><option value="mainnet">Mainnet (Chain ID 304 - https://mainnet.zklighter.elliot.ai)</option><option value="testnet">Testnet (Chain ID 300 - https://testnet.zklighter.elliot.ai)</option></select></label>
          <label class="form-group"><span class="form-label">Lighter Account Index</span><input class="form-input mono" type="number" value={fieldValue(settings, 'account_index')} onInput={(event) => update('account_index', event.currentTarget.value)} placeholder="e.g. 12345" /></label>
          <label class="form-group"><span class="form-label">API Key Index</span><input class="form-input mono" type="number" value={fieldValue(settings, 'api_key_index')} onInput={(event) => update('api_key_index', event.currentTarget.value)} min="4" max="254" /></label>
          <label class="form-group"><span class="form-label">Lighter API Private Key</span><span style={{ display: 'flex', gap: '8px' }}><input class="form-input mono" type={showApiPrivateKey ? 'text' : 'password'} value={settings.api_private_key} onInput={(event) => update('api_private_key', event.currentTarget.value)} placeholder="0x... or leave empty to use server wallet key" autoComplete="off" /><button class="btn-copy" type="button" onClick={() => setShowApiPrivateKey((value) => !value)}>{showApiPrivateKey ? '🔒' : '👁️'}</button></span></label>
        </div></div>
        <div class="settings-section"><div class="settings-section-title">Capital Allocation &amp; Risk Parameters</div><div class="settings-form-grid">
          <NumberField label="Margin Allocation per Trade (%)" value={settings.trade_margin_fraction * 100} onInput={(value) => update('trade_margin_fraction', numberOr(value, 50) / 100)} min="10" max="100" step="5" help="Percentage of account equity allocated as margin." />
          <NumberField label="Leverage Multiplier" value={settings.leverage} onInput={(value) => update('leverage', value)} min="1" max="50" step="1" help="Leverage applied on Lighter (up to 50x for BTC perp)." />
          <NumberField label="Min Lag Trigger ($)" value={settings.min_lag_trigger} onInput={(value) => update('min_lag_trigger', value)} min="1" max="50" step="0.5" help="Minimum divergence between the discovery leader and Lighter." />
          <NumberField label="Max Hold Seconds" value={settings.max_hold_seconds} onInput={(value) => update('max_hold_seconds', value)} min="2" max="60" step="1" help="Maximum position duration before auto-closing." />
          <NumberField label="Simulation Starting Balance ($)" value={settings.simulation_starting_balance} onInput={(value) => update('simulation_starting_balance', value)} min="10" max="1000000" step="10" help="Initial virtual balance for the paper engine." />
        </div></div>
        <div class="settings-section" style={{ borderColor: 'rgba(239, 68, 68, 0.35)' }}><div class="settings-section-title" style={{ color: 'var(--red)' }}>⚠️ Simulation Lifecycle &amp; System Reset</div><p style={{ fontSize: '12px', color: 'var(--text-dim)', marginBottom: '14px', lineHeight: 1.5 }}>Reset paper trades, DUAL comparisons, repricing cycles, and simulation metrics. The operation is blocked while a live Lighter order or position is active.</p><button class="btn btn-danger" disabled={saving} onClick={resetSimulation}>🔄 Reset Simulation to Initial State</button></div>
      </div>
    </section>
  );
}

function NumberField({ label, value, onInput, min, max, step, help }) {
  return <label class="form-group"><span class="form-label">{label}</span><input class="form-input mono" type="number" value={value} onInput={(event) => onInput(event.currentTarget.value)} min={min} max={max} step={step} /><span class="form-help">{help}</span></label>;
}
