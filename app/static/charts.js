// Bounded canvas charts for the dashboard control plane.
(function () {
  function finite(value) {
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function timeLabel(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value || '') : date.toLocaleTimeString([], {
      hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
    });
  }

  class CanvasLineChart {
    constructor(canvas, options = {}) {
      if (!canvas || typeof canvas.getContext !== 'function') {
        throw new Error('CanvasLineChart requires a canvas element.');
      }
      this.canvas = canvas;
      this.context = canvas.getContext('2d');
      this.options = options;
      this.labels = [];
      this.datasets = (options.datasets || []).map((item) => ({ ...item, data: [] }));
      this.lastSignature = '';
      this.frameHandle = null;
      this.paused = false;
      this.resizeObserver = typeof ResizeObserver === 'function'
        ? new ResizeObserver(() => this.requestDraw()) : null;
      if (this.resizeObserver) this.resizeObserver.observe(canvas);
      window.addEventListener('resize', () => this.requestDraw());
      this.requestDraw();
    }

    setData(labels, seriesById) {
      const sourceLabels = Array.isArray(labels) ? labels : [];
      const signature = [sourceLabels.length, sourceLabels.at(-1) || '']
        .concat(this.datasets.map((set) => {
          const values = Array.isArray(seriesById?.[set.id]) ? seriesById[set.id] : [];
          return `${set.id}:${values.length}:${values.at(-1) ?? ''}`;
        })).join('|');
      if (signature === this.lastSignature) return;
      this.lastSignature = signature;
      this.labels = sourceLabels.map((item) => String(item || ''));
      this.datasets.forEach((set) => {
        const values = Array.isArray(seriesById?.[set.id]) ? seriesById[set.id] : [];
        set.data = values.map(finite);
      });
      this.requestDraw();
    }

    setThresholds(thresholds) {
      this.options.thresholds = Array.isArray(thresholds) ? thresholds : [];
      this.lastSignature = '';
      this.requestDraw();
    }

    setPaused(paused) {
      this.paused = Boolean(paused);
      if (!this.paused) this.requestDraw();
    }

    clear() {
      this.labels = [];
      this.datasets.forEach((set) => { set.data = []; });
      this.lastSignature = '';
      this.requestDraw();
    }

    requestDraw() {
      if (this.paused || this.frameHandle !== null) return;
      const draw = () => { this.frameHandle = null; this.draw(); };
      this.frameHandle = window.requestAnimationFrame
        ? window.requestAnimationFrame(draw) : window.setTimeout(draw, 0);
    }

    _legend(width) {
      const context = this.context;
      context.font = '11px "JetBrains Mono", monospace';
      const items = [];
      let x = 0;
      let y = 10;
      this.datasets.forEach((set) => {
        const latest = set.data.filter((item) => item !== null).at(-1);
        const suffix = latest === undefined ? '' : ` ${latest >= 1000 ? latest.toFixed(1) : latest.toFixed(2)}`;
        const label = `${set.label}${suffix}`;
        const itemWidth = context.measureText(label).width + 24;
        if (x && x + itemWidth > width) { x = 0; y += 16; }
        items.push({ set, label, x, y });
        x += itemWidth;
      });
      return { items, height: y + 8 };
    }

    _drawLegend(legend) {
      const context = this.context;
      context.font = '11px "JetBrains Mono", monospace';
      context.textBaseline = 'middle';
      legend.items.forEach(({ set, label, x, y }) => {
        context.strokeStyle = set.color;
        context.lineWidth = 2;
        context.beginPath(); context.moveTo(x, y); context.lineTo(x + 12, y); context.stroke();
        context.fillStyle = set.color;
        context.beginPath(); context.arc(x + 6, y, 2.5, 0, Math.PI * 2); context.fill();
        context.fillStyle = '#cbd5e1'; context.fillText(label, x + 16, y);
      });
    }

    _bounds() {
      let min = Infinity;
      let max = -Infinity;
      this.datasets.forEach((set) => set.data.forEach((value) => {
        if (value !== null) { min = Math.min(min, value); max = Math.max(max, value); }
      }));
      (this.options.thresholds || []).forEach((line) => {
        min = Math.min(min, line.value); max = Math.max(max, line.value);
      });
      if (!Number.isFinite(min)) return null;
      if (min === max) { const pad = Math.max(Math.abs(min) * 0.001, 1); min -= pad; max += pad; }
      const pad = Math.max((max - min) * 0.08, 0.02);
      return { min: min - pad, max: max + pad };
    }

    draw() {
      if (this.paused) return;
      const context = this.context;
      const bounds = this.canvas.getBoundingClientRect();
      const width = Math.max(1, Math.floor(bounds.width || this.canvas.parentElement?.clientWidth || 500));
      const height = Math.max(1, Math.floor(bounds.height || this.canvas.parentElement?.clientHeight || 260));
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      this.canvas.width = Math.floor(width * ratio);
      this.canvas.height = Math.floor(height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      context.clearRect(0, 0, width, height);

      const legend = this._legend(width - 16);
      this._drawLegend(legend);
      const range = this._bounds();
      if (!range) return this._empty(width, height);
      const plot = { left: 64, top: legend.height + 8, right: width - 20, bottom: height - 26 };
      if (plot.bottom <= plot.top + 20 || plot.right <= plot.left + 20) return this._empty(width, height, 'Panel too small');
      const count = Math.max(1, this.labels.length - 1);
      const xFor = (index) => this.labels.length <= 1 ? (plot.left + plot.right) / 2 : plot.left + ((plot.right - plot.left) * index) / count;
      const yFor = (value) => plot.bottom - ((value - range.min) / (range.max - range.min)) * (plot.bottom - plot.top);
      this._grid(context, plot, range, yFor);
      this._thresholds(context, plot, range, yFor);
      this._datasets(context, plot, xFor, yFor);
      this._xLabels(context, plot, xFor);
    }

    _empty(width, height, message = this.options.emptyMessage || 'Waiting for live price updates…') {
      const context = this.context;
      context.fillStyle = '#64748b'; context.font = '12px "JetBrains Mono", monospace';
      context.textAlign = 'center'; context.textBaseline = 'middle';
      context.fillText(message, width / 2, height / 2); context.textAlign = 'left';
    }

    _grid(context, plot, range, yFor) {
      const formatter = this.options.yFormatter || String;
      context.font = '10px "JetBrains Mono", monospace'; context.textBaseline = 'middle';
      for (let tick = 0; tick <= 4; tick += 1) {
        const value = range.min + ((range.max - range.min) * tick) / 4;
        const y = yFor(value);
        context.strokeStyle = '#1e293b'; context.lineWidth = 1;
        context.beginPath(); context.moveTo(plot.left, y); context.lineTo(plot.right, y); context.stroke();
        context.fillStyle = '#64748b'; context.textAlign = 'right'; context.fillText(formatter(value), plot.left - 6, y);
      }
    }

    _thresholds(context, plot, range, yFor) {
      (this.options.thresholds || []).forEach((line) => {
        if (line.value < range.min || line.value > range.max) return;
        const y = yFor(line.value);
        context.save(); context.strokeStyle = line.color || '#10e598'; context.setLineDash([4, 4]);
        context.beginPath(); context.moveTo(plot.left, y); context.lineTo(plot.right, y); context.stroke();
        if (line.label) { context.fillStyle = line.color || '#10e598'; context.font = '9px "JetBrains Mono", monospace'; context.textAlign = 'right'; context.fillText(line.label, plot.right - 4, y - 6); }
        context.restore();
      });
    }

    _datasets(context, plot, xFor, yFor) {
      context.save(); context.beginPath(); context.rect(plot.left, plot.top - 2, plot.right - plot.left, plot.bottom - plot.top + 4); context.clip();
      this.datasets.forEach((set) => {
        let drawing = false;
        let last = null;
        context.strokeStyle = set.color; context.lineWidth = set.lineWidth || 2; context.lineJoin = 'round'; context.lineCap = 'round'; context.beginPath();
        this.labels.forEach((_, index) => {
          const value = set.data[index];
          if (value === null || value === undefined) { drawing = false; return; }
          const point = { x: xFor(index), y: yFor(value) };
          if (drawing) context.lineTo(point.x, point.y); else context.moveTo(point.x, point.y);
          drawing = true; last = point;
        });
        context.stroke();
        if (last) { context.fillStyle = set.color; context.beginPath(); context.arc(last.x, last.y, 3.5, 0, Math.PI * 2); context.fill(); }
      });
      context.restore();
    }

    _xLabels(context, plot, xFor) {
      const count = Math.min(6, this.labels.length);
      context.fillStyle = '#64748b'; context.font = '10px "JetBrains Mono", monospace'; context.textAlign = 'center'; context.textBaseline = 'top';
      for (let position = 0; position < count; position += 1) {
        const index = count === 1 ? 0 : Math.round((position * (this.labels.length - 1)) / (count - 1));
        context.fillText(timeLabel(this.labels[index]), xFor(index), plot.bottom + 6);
      }
      context.textAlign = 'left';
    }
  }

  window.CanvasLineChart = CanvasLineChart;
}());
