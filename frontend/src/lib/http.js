function errorMessage(payload, status) {
  if (payload && typeof payload === 'object' && typeof payload.detail === 'string') {
    return payload.detail;
  }
  return `HTTP ${status}`;
}

export async function requestJson(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  const payload = await response.json().catch(() => null);
  if (!response.ok) throw new Error(errorMessage(payload, response.status));
  return payload;
}

export async function copyToClipboard(value) {
  if (!value || !navigator.clipboard) throw new Error('Clipboard access is unavailable.');
  await navigator.clipboard.writeText(String(value));
}

export function jsonHeaders() {
  return { 'Content-Type': 'application/json' };
}
