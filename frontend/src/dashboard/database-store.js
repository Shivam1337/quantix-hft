import { useEffect, useState } from 'preact/hooks';
import { requestJson } from '../lib/http.js';

const initialState = Object.freeze({
  backend: 'SQLite',
  formatted: '-- MB',
  checkedAt: '',
  loading: false,
});

class DatabaseStore {
  constructor() {
    this.state = initialState;
    this.listeners = new Set();
  }

  getState = () => this.state;

  subscribe = (listener) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  publish() {
    this.listeners.forEach((listener) => listener(this.state));
  }

  async refresh() {
    if (this.state.loading) return;
    this.state = { ...this.state, loading: true };
    this.publish();
    try {
      const data = await requestJson('/api/system/database-size');
      const checked = new Date(data.checked_at);
      this.state = {
        backend: data.backend === 'sqlite' ? 'SQLite (dev)' : data.backend ? 'PostgreSQL' : '--',
        formatted: data.formatted || '-- MB',
        checkedAt: Number.isNaN(checked.getTime()) ? data.checked_at || '' : checked.toLocaleTimeString([], {
          hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
        }),
        loading: false,
      };
    } catch (error) {
      this.state = { ...this.state, formatted: 'Err', loading: false };
    }
    this.publish();
  }
}

const databaseStore = new DatabaseStore();

export function useDatabaseSize() {
  const [state, setState] = useState(databaseStore.getState);
  useEffect(() => {
    const unsubscribe = databaseStore.subscribe(setState);
    if (databaseStore.getState().formatted === '-- MB') void databaseStore.refresh();
    return unsubscribe;
  }, []);
  return [state, () => databaseStore.refresh()];
}
