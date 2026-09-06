import { useEffect, useState } from 'preact/hooks';
import { requestJson } from '../lib/http.js';

const INITIAL_STATE = Object.freeze({
  stream: { status: 'INITIALIZING', receivedSnapshot: false },
});

function nextFrame(callback) {
  if (typeof globalThis.requestAnimationFrame === 'function') {
    return globalThis.requestAnimationFrame(callback);
  }
  return globalThis.setTimeout(callback, 0);
}

export class DashboardStore {
  constructor() {
    this.state = INITIAL_STATE;
    this.listeners = new Set();
    this.source = null;
    this.started = false;
    this.frameScheduled = false;
  }

  getState = () => this.state;

  subscribe = (listener) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  start() {
    if (this.started) return;
    this.started = true;
    if (typeof globalThis.EventSource !== 'function') {
      this.setStreamStatus('HTTP_FALLBACK');
      void this.hydrate();
      return;
    }

    this.source = new globalThis.EventSource('/api/system/stream');
    this.source.onopen = () => this.setStreamStatus('CONNECTED');
    this.source.onerror = () => this.setStreamStatus('RECONNECTING');
    ['snapshot', 'tick', 'detail'].forEach((kind) => {
      this.source.addEventListener(kind, (event) => this.parseFrame(kind, event));
    });
    this.source.onmessage = (event) => this.parseFrame('snapshot', event);
    globalThis.setTimeout(() => {
      if (!this.state.stream.receivedSnapshot) void this.hydrate();
    }, 3000);
  }

  close() {
    this.source?.close();
    this.source = null;
    this.started = false;
  }

  parseFrame(kind, event) {
    try {
      this.applyFrame(kind, JSON.parse(event.data));
    } catch (error) {
      console.warn(`Discarded invalid dashboard ${kind} frame.`, error);
    }
  }

  applyFrame(kind, payload) {
    if (!payload || typeof payload !== 'object') return;
    const previousSystem = this.state.system || {};
    this.state = {
      ...this.state,
      ...payload,
      system: payload.system ? { ...previousSystem, ...payload.system } : previousSystem,
      stream: {
        ...this.state.stream,
        status: 'CONNECTED',
        receivedSnapshot: this.state.stream.receivedSnapshot || kind === 'snapshot',
        lastFrameKind: kind,
        lastFrameAt: Date.now(),
      },
    };
    this.schedulePublish();
  }

  async hydrate() {
    try {
      this.applyFrame('snapshot', await requestJson('/api/system/dashboard'));
    } catch (error) {
      this.setStreamStatus('UNAVAILABLE', error.message);
    }
  }

  setStreamStatus(status, error = null) {
    this.state = {
      ...this.state,
      stream: { ...this.state.stream, status, error },
    };
    this.schedulePublish();
  }

  schedulePublish() {
    if (this.frameScheduled) return;
    this.frameScheduled = true;
    nextFrame(() => {
      this.frameScheduled = false;
      this.listeners.forEach((listener) => listener(this.state));
    });
  }
}

export const dashboardStore = new DashboardStore();

export function startDashboardStream() {
  dashboardStore.start();
}

export function useDashboardState() {
  const [state, setState] = useState(dashboardStore.getState);
  useEffect(() => dashboardStore.subscribe(setState), []);
  return state;
}
