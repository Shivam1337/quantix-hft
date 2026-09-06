export function number(value, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '--';
  return parsed.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function money(value, digits = 2) {
  return `$${number(value, digits)}`;
}

export function signedMoney(value, digits = 2) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '--';
  return `${parsed >= 0 ? '+' : ''}${money(parsed, digits)}`;
}

export function percent(value, digits = 1) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${parsed.toFixed(digits)}%` : '--';
}

export function bytes(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '--';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let amount = parsed;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toLocaleString('en-US', {
    maximumFractionDigits: amount >= 10 ? 1 : 2,
  })} ${units[unit]}`;
}

export function duration(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return [hours, minutes, remainder].map((part) => String(part).padStart(2, '0')).join(':');
}

export function localTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '--';
  return date.toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  });
}

export function quoteSize(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? `${number(parsed, 5)} BTC` : '-- BTC';
}

export function finite(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed);
}
