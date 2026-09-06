import assert from 'node:assert/strict';
import test from 'node:test';
import { DashboardStore } from '../src/dashboard/store.js';

function restoreFrameScheduler(previous) {
  if (previous === undefined) delete globalThis.requestAnimationFrame;
  else globalThis.requestAnimationFrame = previous;
}

test('coalesces received SSE frames into one latest-state publish', () => {
  const previousScheduler = globalThis.requestAnimationFrame;
  const callbacks = [];
  globalThis.requestAnimationFrame = (callback) => {
    callbacks.push(callback);
    return callbacks.length;
  };
  try {
    const store = new DashboardStore();
    const published = [];
    store.subscribe((state) => published.push(state));

    store.applyFrame('tick', {
      market: { lighter: { mid_price: 100 } },
      system: { status: 'HEALTHY', streaming_feeds: 6 },
    });
    store.applyFrame('detail', {
      chart: { timestamps: ['one'], lighter_series: [100] },
      system: { resources: { process_cpu_percent: 2.5 } },
    });
    store.applyFrame('tick', {
      market: { lighter: { mid_price: 101 } },
      system: { tick_rate_hz: 4 },
    });

    assert.equal(callbacks.length, 1);
    callbacks[0]();
    assert.equal(published.length, 1);
    assert.equal(published[0].market.lighter.mid_price, 101);
    assert.equal(published[0].chart.timestamps.length, 1);
    assert.equal(published[0].system.status, 'HEALTHY');
    assert.equal(published[0].system.resources.process_cpu_percent, 2.5);
    assert.equal(published[0].system.tick_rate_hz, 4);
  } finally {
    restoreFrameScheduler(previousScheduler);
  }
});
