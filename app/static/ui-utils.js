export function fmt(value, digits = 2) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '--';
  return number.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let amount = number;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toLocaleString('en-US', { maximumFractionDigits: amount >= 10 ? 1 : 2 })} ${units[unit]}`;
}

export function fmtPercent(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(digits)}%` : '--';
}

export function currencyTick(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '--';
  return `$${number.toLocaleString('en-US', { maximumFractionDigits: Math.abs(number) >= 1000 ? 0 : 2 })}`;
}

export function fmtDuration(value) {
  const seconds = Math.max(0, Math.floor(Number(value)) || 0);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours, minutes, remainder].map((part) => String(part).padStart(2, '0')).join(':');
}

export function topBookSize(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? `${fmt(number, 5)} BTC` : '-- BTC';
}

export function setText(target, value) {
  const element = typeof target === 'string' ? document.getElementById(target) : target;
  if (!element) return null;
  const next = String(value ?? '');
  if (element.textContent !== next) element.textContent = next;
  return element;
}

export function setHtml(id, value) {
  const element = document.getElementById(id);
  if (element && element.innerHTML !== value) element.innerHTML = value;
  return element;
}

export function displayUtcTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

export function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  window.setTimeout(() => toast.remove(), 4000);
}

export function copyText(text, button) {
  if (!text || String(text).includes('---') || !navigator.clipboard) return;
  navigator.clipboard.writeText(text).then(() => {
    const original = button?.innerText;
    if (button) button.innerText = '✅ Copied!';
    window.setTimeout(() => { if (button) button.innerText = original; }, 1500);
  }).catch(() => showToast('Failed to copy to clipboard', 'error'));
}

export function toggleInputVisibility(inputId, button) {
  const input = document.getElementById(inputId);
  if (!input) return;
  const visible = input.type === 'password';
  input.type = visible ? 'text' : 'password';
  if (button) button.innerText = visible ? '🔒' : '👁️';
}

export function exposeUtilityActions() {
  Object.assign(window, { copyText, toggleInputVisibility });
}
