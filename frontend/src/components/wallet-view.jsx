import { useEffect, useState } from 'preact/hooks';
import { copyToClipboard, jsonHeaders, requestJson } from '../lib/http.js';
import { money, number } from '../lib/format.js';
import { useToast } from '../ui/toasts.jsx';
import { WalletImportModal } from './wallet-import-modal.jsx';

const MASK = '••••••••••••••••••••••••••••••••';

function Badge({ children, status }) {
  return <span class={`badge ${status === 'ACTIVE' || status === 'READY' ? 'badge-success' : 'badge-warning'}`}>{children}</span>;
}

export function WalletView() {
  const toast = useToast();
  const [wallet, setWallet] = useState(null);
  const [revealed, setRevealed] = useState(null);
  const [showL1, setShowL1] = useState(false);
  const [showLighter, setShowLighter] = useState(false);
  const [importing, setImporting] = useState(false);
  const [loading, setLoading] = useState(false);

  async function load(url = '/api/wallet', options) {
    const data = await requestJson(url, options);
    setWallet(data);
    return data;
  }

  useEffect(() => {
    load().catch((error) => toast(`Failed to load wallet: ${error.message}`, 'error'));
  }, []);

  async function refresh() {
    setLoading(true);
    try {
      await load('/api/wallet/refresh', { method: 'POST' });
      toast('Balances updated.', 'success');
    } catch (error) {
      toast(`Failed to refresh balances: ${error.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  async function getRevealed() {
    if (revealed) return revealed;
    const data = await requestJson('/api/wallet/reveal');
    setRevealed(data);
    return data;
  }

  async function copy(value) {
    try {
      await copyToClipboard(value);
      toast('Copied to clipboard.', 'success');
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  async function copyPrivate(key) {
    try {
      await copy((await getRevealed())[key]);
    } catch (error) {
      toast(`Failed to reveal key: ${error.message}`, 'error');
    }
  }

  async function toggleReveal(kind) {
    const isL1 = kind === 'l1';
    const visible = isL1 ? showL1 : showLighter;
    try {
      if (!visible) await getRevealed();
      if (isL1) setShowL1((value) => !value);
      else setShowLighter((value) => !value);
    } catch (error) {
      toast(`Failed to reveal key: ${error.message}`, 'error');
    }
  }

  async function generate() {
    if (!window.confirm('Generate a new server wallet? This replaces the current wallet keys. Export an existing funded key first.')) return;
    setLoading(true);
    try {
      await load('/api/wallet/generate', { method: 'POST' });
      setRevealed(null); setShowL1(false); setShowLighter(false);
      toast('Generated a new server wallet.', 'success');
    } catch (error) {
      toast(`Wallet generation failed: ${error.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  async function importKey(privateKey) {
    const data = await requestJson('/api/wallet/import', {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ private_key: privateKey }),
    });
    setWallet(data); setRevealed(null); setShowL1(false); setShowLighter(false);
    toast('Wallet imported successfully.', 'success');
  }

  const balances = wallet?.balances || {};
  const collateral = balances.lighter_collateral_usd ?? 0;
  const freeMargin = balances.lighter_free_margin_usd ?? 0;
  const accountIndex = wallet?.lighter_account_index || balances.lighter_account_index;
  const accountStatus = balances.lighter_account_status || (accountIndex ? 'ACTIVE' : 'UNREGISTERED');
  const funded = collateral > 0;
  const l1Key = showL1 ? revealed?.private_key || MASK : MASK;
  const lighterKey = showLighter ? revealed?.lighter_private_key || MASK : MASK;
  return (
    <section id="view-wallet" class="view-pane">
      <div class="view-content-wrapper">
        <div class="view-header"><div><div class="view-title">👛 Server Wallet &amp; Lighter zkRollup Balances</div><div class="view-subtitle">Maintained on the server. Fund with USDC to enable live zero-fee execution.</div></div><div class="view-actions"><button class="btn btn-secondary" disabled={loading} onClick={refresh}>🔄 Refresh Balances</button><button class="btn btn-secondary" onClick={() => setImporting(true)}>📥 Import Key</button><button class="btn btn-danger" disabled={loading} onClick={generate}>➕ New Wallet</button></div></div>
        <div class="wallet-cards-grid">
          <div class="wallet-card highlight"><div class="wallet-card-header"><span class="wallet-card-label">LIGHTER COLLATERAL (USDC)</span><Badge status={accountStatus}>{accountStatus}</Badge></div><div class="wallet-card-val mono">{money(collateral)}</div><div class="wallet-card-sub mono"><span>Free Margin: <strong>{money(freeMargin)}</strong></span><span>Account: <strong>{accountIndex ? `#${accountIndex}` : 'None'}</strong></span></div></div>
          <div class="wallet-card"><div class="wallet-card-header"><span class="wallet-card-label">ARBITRUM GAS (ETH)</span><span class="badge badge-info">Arbitrum One</span></div><div class="wallet-card-val mono">{number(balances.arbitrum_eth ?? 0, 6)} ETH</div><div class="wallet-card-sub mono"><span>Network: <strong>Arbitrum (42161)</strong></span><span>Sync: {balances.last_checked || '--:--:--'}</span></div></div>
          <div class="wallet-card"><div class="wallet-card-header"><span class="wallet-card-label">TRADING READINESS</span><Badge status={funded ? 'READY' : 'WAITING'}>{funded ? 'REAL READY' : 'SIMULATION ONLY'}</Badge></div><div class="wallet-card-val" style={{ fontSize: '18px', color: funded ? 'var(--green)' : 'var(--orange)' }}>{funded ? 'Ready for Live Trading' : 'Awaiting Collateral'}</div><div class="wallet-card-sub"><span>{funded ? `Collateral active: ${money(collateral)} USDC on zkLighter` : 'Deposit USDC on Lighter to enable live trading'}</span></div></div>
        </div>
        <div class="credentials-card"><div class="credentials-card-title">🔐 Wallet Keys &amp; zk-Signer Credentials</div>
          <Credential label="Ethereum L1 Address (Public Key / Deposit Address)" value={wallet?.address || '0x----------------------------------------'} actions={<button class="btn-copy" onClick={() => copy(wallet?.address)}>📋 Copy Address</button>} />
          <Credential label="Ethereum L1 Private Key (Use to import into MetaMask / Rabby)" value={l1Key} actions={<><button class="btn-copy" onClick={() => toggleReveal('l1')}>{showL1 ? '🔒 Hide' : '👁️ Show'}</button><button class="btn-copy" onClick={() => copyPrivate('private_key')}>📋 Copy Key</button></>} />
          <Credential label="Lighter zk-Signer Public Key" value={wallet?.lighter_public_key || '0x----------------------------------------'} actions={<button class="btn-copy" onClick={() => copy(wallet?.lighter_public_key)}>📋 Copy Key</button>} />
          <Credential label="Lighter zk-Signer Private Key (For API Order Signing)" value={lighterKey} actions={<><button class="btn-copy" onClick={() => toggleReveal('lighter')}>{showLighter ? '🔒 Hide' : '👁️ Show'}</button><button class="btn-copy" onClick={() => copyPrivate('lighter_private_key')}>📋 Copy Key</button></>} />
        </div>
        <div class="guide-box"><div class="guide-box-title">📖 Quick Onboarding &amp; Deposit Guide</div><div class="guide-steps-grid"><GuideStep number="1"><strong>Copy Address:</strong> Copy your Ethereum L1 address above.</GuideStep><GuideStep number="2"><strong>Fund with USDC:</strong> Send USDC and ETH for gas on <em>Arbitrum One</em>.</GuideStep><GuideStep number="3"><strong>Deposit to Lighter:</strong> Go to <a href="https://app.lighter.xyz" target="_blank" rel="noreferrer" style={{ color: 'var(--cyan)' }}>app.lighter.xyz</a> and deposit USDC into zkLighter.</GuideStep><GuideStep number="4"><strong>Enable Real Mode:</strong> Refresh balances, then select REAL or DUAL in Settings.</GuideStep></div></div>
      </div>
      {importing && <WalletImportModal onClose={() => setImporting(false)} onImport={importKey} />}
    </section>
  );
}

function Credential({ label, value, actions }) {
  return <div class="credential-row"><div class="credential-info"><div class="credential-label">{label}</div><div class="credential-value mono">{value}</div></div><div class="credential-actions">{actions}</div></div>;
}

function GuideStep({ number: step, children }) {
  return <div class="guide-step"><div class="step-num">{step}</div><div class="step-text">{children}</div></div>;
}
