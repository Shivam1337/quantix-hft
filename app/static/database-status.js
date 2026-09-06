import { setText } from './ui-utils.js';

export async function fetchDbSize() {
  const value = document.getElementById('db-size-val');
  if (!value) return;
  try {
    const response = await fetch('/api/system/database-size', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    setText(value, data.formatted || (data.size_mb ? `${data.size_mb.toFixed(2)} MB` : '-- MB'));
    setText('db-size-backend', data.backend === 'sqlite' ? 'SQLite (dev)' : data.backend ? 'PostgreSQL' : '--');
    if (data.checked_at) {
      const checked = new Date(data.checked_at);
      setText('db-checked-at', Number.isNaN(checked.getTime()) ? data.checked_at : `@ ${checked.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}`);
    }
  } catch (error) {
    console.warn('Failed to query database size:', error);
    setText(value, 'Err');
  }
  setText('sidebar-db-size-display', value.textContent);
}

export async function refreshDbSize() {
  const button = document.getElementById('refresh-db-btn');
  if (button) { button.disabled = true; button.textContent = '⌛...'; }
  await fetchDbSize();
  if (button) { button.disabled = false; button.textContent = '🔄 Refresh'; }
}

export function exposeDatabaseActions() {
  window.refreshDbSize = refreshDbSize;
}
