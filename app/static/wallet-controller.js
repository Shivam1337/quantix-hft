import { copyText, fmt, setText, showToast } from './ui-utils.js';

let unmaskedWallet = null;
let l1Revealed = false;
let lighterRevealed = false;

function setBadge(element, text, className) {
  if (!element) return;
  setText(element, text);
  element.className = className;
}

export function renderWalletView(data) {
  const balances = data.balances || {};
  const collateral = balances.lighter_collateral_usd ?? 0;
  const freeMargin = balances.lighter_free_margin_usd ?? 0;
  const accountIndex = data.lighter_account_index || balances.lighter_account_index;
  const accountStatus = balances.lighter_account_status || (accountIndex ? 'ACTIVE' : 'UNREGISTERED');
  setText('wallet-lighter-collateral', `$${fmt(collateral, 2)}`);
  setText('wallet-lighter-free', `$${fmt(freeMargin, 2)}`);
  setText('wallet-lighter-acc-idx', accountIndex ? `#${accountIndex}` : 'None');
  setBadge(document.getElementById('wallet-lighter-status'), accountStatus, `badge ${accountStatus === 'ACTIVE' ? 'badge-success' : 'badge-warning'}`);
  setText('wallet-arb-eth', `${fmt(balances.arbitrum_eth ?? 0, 6)} ETH`);
  setText('wallet-last-checked', `Sync: ${balances.last_checked || 'Just now'}`);

  const funded = collateral > 0;
  const readiness = document.getElementById('wallet-readiness-val');
  if (readiness) readiness.style.color = funded ? 'var(--green)' : 'var(--orange)';
  setText(readiness, funded ? 'Ready for Live Trading' : 'Awaiting Collateral');
  setText('wallet-readiness-msg', funded ? `Collateral active: $${fmt(collateral, 2)} USDC on zkLighter` : 'Deposit USDC on Lighter to enable live trading');
  setBadge(document.getElementById('wallet-readiness-badge'), funded ? 'REAL READY' : 'SIMULATION ONLY', `badge ${funded ? 'badge-success' : 'badge-warning'}`);

  setText('wallet-l1-address', data.address || '0x--');
  if (!l1Revealed) setText('wallet-l1-privkey', data.private_key || '••••••••••••••••••••••••••••••••');
  setText('wallet-lighter-pubkey', data.lighter_public_key || '0x--');
  if (!lighterRevealed) setText('wallet-lighter-privkey', data.lighter_private_key || '••••••••••••••••••••••••••••••••');
}

export async function loadWalletData() {
  try {
    const response = await fetch('/api/wallet', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderWalletView(await response.json());
  } catch (error) {
    console.warn('Failed to load wallet data:', error);
    showToast('Failed to load wallet data', 'error');
  }
}

export async function refreshWalletData() {
  showToast('Refreshing on-chain balances...', 'info');
  try {
    const response = await fetch('/api/wallet/refresh', { method: 'POST' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderWalletView(await response.json());
    showToast('Balances updated successfully!', 'success');
  } catch (error) {
    showToast(`Failed to refresh balances: ${error.message}`, 'error');
  }
}

async function revealWallet() {
  if (!unmaskedWallet) {
    const response = await fetch('/api/wallet/reveal');
    if (!response.ok) throw new Error('Failed to fetch credentials');
    unmaskedWallet = await response.json();
  }
  return unmaskedWallet;
}

export async function toggleRevealKey(type, button) {
  try {
    const wallet = await revealWallet();
    const isL1 = type === 'l1';
    const revealed = isL1 ? l1Revealed : lighterRevealed;
    const element = document.getElementById(isL1 ? 'wallet-l1-privkey' : 'wallet-lighter-privkey');
    const value = isL1 ? wallet.private_key : wallet.lighter_private_key;
    setText(element, revealed ? '••••••••••••••••••••••••••••••••' : value);
    if (button) button.innerText = revealed ? '👁️ Show' : '🔒 Hide';
    if (isL1) l1Revealed = !revealed; else lighterRevealed = !revealed;
  } catch (error) {
    showToast('Error revealing key', 'error');
  }
}

export async function copyUnmaskedKey(name, button) {
  try {
    const wallet = await revealWallet();
    const value = wallet[name];
    if (value) copyText(value, button);
  } catch (error) {
    showToast('Failed to copy key', 'error');
  }
}

export async function confirmGenerateWallet() {
  if (!window.confirm('⚠️ WARNING: Generate a brand new server wallet?\n\nThis will replace the current wallet keys. Make sure you have exported your existing private key if it holds funds!')) return;
  try {
    const response = await fetch('/api/wallet/generate', { method: 'POST' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    unmaskedWallet = null; l1Revealed = false; lighterRevealed = false;
    renderWalletView(await response.json());
    showToast('Generated new server wallet successfully!', 'success');
  } catch (error) {
    showToast(`Generated wallet failed: ${error.message}`, 'error');
  }
}

export function showImportWalletModal() {
  const input = document.getElementById('import-privkey-input');
  if (input) input.value = '';
  setText('import-error-msg', '');
  const modal = document.getElementById('import-wallet-modal');
  if (modal) modal.style.display = 'flex';
}

export function closeImportWalletModal() {
  const modal = document.getElementById('import-wallet-modal');
  if (modal) modal.style.display = 'none';
}

export async function submitImportWallet() {
  const input = document.getElementById('import-privkey-input');
  const key = input?.value.trim() || '';
  if (key.length < 32) return setText('import-error-msg', 'Please enter a valid Ethereum private key (hex format).');
  try {
    setText('import-error-msg', 'Importing...');
    const response = await fetch('/api/wallet/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ private_key: key }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || 'Import failed');
    unmaskedWallet = null; l1Revealed = false; lighterRevealed = false;
    renderWalletView(data); closeImportWalletModal(); showToast('Wallet imported successfully!', 'success');
  } catch (error) {
    setText('import-error-msg', error.message);
  }
}

export function exposeWalletActions() {
  Object.assign(window, {
    confirmGenerateWallet, copyUnmaskedKey, closeImportWalletModal, refreshWalletData,
    showImportWalletModal, submitImportWallet, toggleRevealKey,
  });
}
