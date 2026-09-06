// Lightweight high-performance canvas line charts with terminal styling.
(function () {
  function finiteNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function displayTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value || '');
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
  }

  class CanvasLineChart {
    constructor(canvas, options) {
      if (!canvas || typeof canvas.getContext !== 'function') {
        throw new Error('CanvasLineChart requires a canvas element.');
      }
      this.canvas = canvas;
      this.context = canvas.getContext('2d');
      this.options = options || {};
      this.labels = [];
      this.datasets = (this.options.datasets || []).map((dataset) => ({ ...dataset, data: [] }));
      this.lastSignature = null;
      this.frameHandle = null;
      this.resizeObserver = typeof ResizeObserver === 'function'
        ? new ResizeObserver(() => this.requestDraw())
        : null;

      if (this.resizeObserver) this.resizeObserver.observe(canvas);
      window.addEventListener('resize', () => this.requestDraw());
      this.requestDraw();
    }

    setData(labels, seriesById) {
      this.labels = Array.isArray(labels) ? labels.map((label) => String(label || '')) : [];
      this.datasets.forEach((dataset) => {
        const values = seriesById && Array.isArray(seriesById[dataset.id]) ? seriesById[dataset.id] : [];
        dataset.data = values.map(finiteNumber);
      });

      const signature = [
        this.labels.length,
        this.labels[this.labels.length - 1] || '',
        ...this.datasets.map((dataset) => {
          const values = dataset.data;
          return `${dataset.id}:${values.length}:${values[values.length - 1] ?? ''}`;
        }),
      ].join('|');
      if (signature === this.lastSignature) return;
      this.lastSignature = signature;
      this.requestDraw();
    }

    setThresholds(thresholds) {
      this.options.thresholds = Array.isArray(thresholds) ? thresholds : [];
      this.lastSignature = null;
      this.requestDraw();
    }

    requestDraw() {
      if (this.frameHandle !== null) return;
      const draw = () => {
        this.frameHandle = null;
        this.draw();
      };
      this.frameHandle = typeof window.requestAnimationFrame === 'function'
        ? window.requestAnimationFrame(draw)
        : window.setTimeout(draw, 0);
    }

    legendLayout(width) {
      const context = this.context;
      context.font = '11px "JetBrains Mono", monospace';
      const items = [];
      let x = 0;
      let y = 10;
      this.datasets.forEach((dataset) => {
        const lastVal = dataset.data.filter((v) => v !== null).slice(-1)[0];
        const valText = lastVal !== undefined && lastVal !== null ? ` ${lastVal >= 1000 ? lastVal.toFixed(1) : lastVal.toFixed(2)}` : '';
        const label = `${dataset.label}${valText}`;
        const itemWidth = context.measureText(label).width + 24;
        if (x > 0 && x + itemWidth > width) {
          x = 0;
          y += 16;
        }
        items.push({ dataset, label, x, y });
        x += itemWidth;
      });
      return { items, height: y + 8 };
    }

    drawLegend(layout) {
      const context = this.context;
      context.font = '11px "JetBrains Mono", monospace';
      context.textBaseline = 'middle';
      layout.items.forEach(({ dataset, label, x, y }) => {
        context.strokeStyle = dataset.color;
        context.lineWidth = 2;
        context.beginPath();
        context.moveTo(x, y);
        context.lineTo(x + 12, y);
        context.stroke();

        context.fillStyle = dataset.color;
        context.beginPath();
        context.arc(x + 6, y, 2.5, 0, Math.PI * 2);
        context.fill();

        context.fillStyle = '#cbd5e1';
        context.fillText(label, x + 16, y);
      });
    }

    drawEmpty(width, height, message) {
      const context = this.context;
      context.fillStyle = '#64748b';
      context.font = '12px "JetBrains Mono", monospace';
      context.textAlign = 'center';
      context.textBaseline = 'middle';
      context.fillText(message || 'Waiting for live price updates…', width / 2, height / 2);
      context.textAlign = 'left';
    }

    draw() {
      const context = this.context;
      const bounds = this.canvas.getBoundingClientRect();
      const width = Math.max(1, Math.floor(bounds.width || this.canvas.parentElement?.clientWidth || 500));
      const height = Math.max(1, Math.floor(bounds.height || this.canvas.parentElement?.clientHeight || 260));
      const devicePixelRatio = Math.min(window.devicePixelRatio || 1, 2);

      this.canvas.width = Math.floor(width * devicePixelRatio);
      this.canvas.height = Math.floor(height * devicePixelRatio);
      context.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
      context.clearRect(0, 0, width, height);

      const legend = this.legendLayout(width - 16);
      this.drawLegend(legend);

      const values = this.datasets.flatMap((dataset) => dataset.data).filter((value) => value !== null);
      if (!values.length) {
        this.drawEmpty(width, height, this.options.emptyMessage);
        return;
      }

      let minimum = Math.min(...values);
      let maximum = Math.max(...values);

      // Account for thresholds in scale
      if (Array.isArray(this.options.thresholds)) {
        this.options.thresholds.forEach((t) => {
          minimum = Math.min(minimum, t.value);
          maximum = Math.max(maximum, t.value);
        });
      }

      if (minimum === maximum) {
        const adjustment = Math.max(Math.abs(minimum) * 0.001, 1);
        minimum -= adjustment;
        maximum += adjustment;
      }
      const padding = Math.max((maximum - minimum) * 0.08, 0.02);
      minimum -= padding;
      maximum += padding;

      const plot = {
        left: 64,
        top: legend.height + 8,
        right: width - 20,
        bottom: height - 26,
      };
      if (plot.bottom <= plot.top + 20 || plot.right <= plot.left + 20) {
        this.drawEmpty(width, height, 'Panel too small');
        return;
      }

      const xFor = (index) => {
        const count = Math.max(1, this.labels.length - 1);
        return this.labels.length <= 1
          ? (plot.left + plot.right) / 2
          : plot.left + ((plot.right - plot.left) * index) / count;
      };
      const yFor = (value) => plot.bottom - ((value - minimum) / (maximum - minimum)) * (plot.bottom - plot.top);
      const formatter = this.options.yFormatter || ((value) => String(value));
      const yTicks = 4;

      // Draw Grid Lines & Y-axis Labels
      context.font = '10px "JetBrains Mono", monospace';
      context.textBaseline = 'middle';
      for (let tick = 0; tick <= yTicks; tick += 1) {
        const fraction = tick / yTicks;
        const y = plot.bottom - fraction * (plot.bottom - plot.top);
        const value = minimum + fraction * (maximum - minimum);
        context.strokeStyle = '#1e293b';
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(plot.left, y);
        context.lineTo(plot.right, y);
        context.stroke();
        context.fillStyle = '#64748b';
        context.textAlign = 'right';
        context.fillText(formatter(value), plot.left - 6, y);
      }

      // Draw Optional Threshold Lines (e.g. $6 snipe trigger, $0 parity)
      if (Array.isArray(this.options.thresholds)) {
        this.options.thresholds.forEach((t) => {
          if (t.value >= minimum && t.value <= maximum) {
            const y = yFor(t.value);
            context.save();
            context.strokeStyle = t.color || '#10e598';
            context.lineWidth = 1;
            context.setLineDash([4, 4]);
            context.beginPath();
            context.moveTo(plot.left, y);
            context.lineTo(plot.right, y);
            context.stroke();
            if (t.label) {
              context.fillStyle = t.color || '#10e598';
              context.font = '9px "JetBrains Mono", monospace';
              context.textAlign = 'right';
              context.fillText(t.label, plot.right - 4, y - 6);
            }
            context.restore();
          }
        });
      }

      // Clip and Draw Datasets
      context.save();
      context.beginPath();
      context.rect(plot.left, plot.top - 2, plot.right - plot.left, plot.bottom - plot.top + 4);
      context.clip();

      this.datasets.forEach((dataset) => {
        let drawing = false;
        let drawnCount = 0;
        let lastPoint = null;

        context.strokeStyle = dataset.color;
        context.lineWidth = dataset.lineWidth || 2;
        context.lineJoin = 'round';
        context.lineCap = 'round';
        context.beginPath();

        this.labels.forEach((_, index) => {
          const value = dataset.data[index];
          if (value === null || value === undefined) {
            drawing = false;
            return;
          }
          const x = xFor(index);
          const y = yFor(value);
          if (drawing) {
            context.lineTo(x, y);
          } else {
            context.moveTo(x, y);
          }
          drawing = true;
          drawnCount += 1;
          lastPoint = { x, y, value };
        });
        context.stroke();

        // If only 1 sample exists, draw a visible marker & horizontal reference line
        if (drawnCount === 1 && lastPoint) {
          context.fillStyle = dataset.color;
          context.beginPath();
          context.arc(lastPoint.x, lastPoint.y, 4, 0, Math.PI * 2);
          context.fill();

          context.save();
          context.strokeStyle = dataset.color;
          context.setLineDash([2, 4]);
          context.globalAlpha = 0.5;
          context.beginPath();
          context.moveTo(plot.left, lastPoint.y);
          context.lineTo(plot.right, lastPoint.y);
          context.stroke();
          context.restore();
        } else if (drawnCount > 1 && lastPoint) {
          // Draw a distinct live pulse dot on the latest point
          context.fillStyle = dataset.color;
          context.beginPath();
          context.arc(lastPoint.x, lastPoint.y, 3.5, 0, Math.PI * 2);
          context.fill();

          context.strokeStyle = '#ffffff';
          context.lineWidth = 1;
          context.beginPath();
          context.arc(lastPoint.x, lastPoint.y, 4.5, 0, Math.PI * 2);
          context.stroke();
        }
      });
      context.restore();

      // Draw X-axis Time Labels
      const labelCount = Math.min(6, this.labels.length);
      context.fillStyle = '#64748b';
      context.font = '10px "JetBrains Mono", monospace';
      context.textAlign = 'center';
      context.textBaseline = 'top';
      for (let position = 0; position < labelCount; position += 1) {
        const index = labelCount === 1
          ? 0
          : Math.round((position * (this.labels.length - 1)) / (labelCount - 1));
        context.fillText(displayTime(this.labels[index]), xFor(index), plot.bottom + 6);
      }
      context.textAlign = 'left';
    }
  }

  window.CanvasLineChart = CanvasLineChart;
}());
