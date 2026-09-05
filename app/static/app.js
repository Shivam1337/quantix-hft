// app/static/app.js - Real-time Dashboard Controller

let equityChart = null;
const equityHistory = [];
const labelsHistory = [];

document.addEventListener('DOMContentLoaded', () => {
    initChart();
    initEventStream();
    loadTrades();
});

// Initialize Chart.js Equity Curve
function initChart() {
    const ctx = document.getElementById('equityChart').getContext('2d');
    equityChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labelsHistory,
            datasets: [{
                label: 'Portfolio Equity ($)',
                data: equityHistory,
                borderColor: '#10b981',
                backgroundColor: 'rgba(16, 185, 129, 0.1)',
                borderWidth: 2,
                fill: true,
                tension: 0.3,
                pointRadius: 1,
                pointHoverRadius: 4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: (ctx) => `Equity: $${ctx.parsed.y.toFixed(2)}`
                    }
                }
            },
            scales: {
                x: {
                    display: false,
                    grid: { display: false }
                },
                y: {
                    grid: { color: 'rgba(51, 65, 85, 0.4)' },
                    ticks: {
                        color: '#94a3b8',
                        font: { family: 'monospace', size: 10 },
                        callback: (v) => `$${v.toFixed(2)}`
                    }
                }
            }
        }
    });
}

// Connect to Server-Sent Events (SSE) stream for zero-latency live updates
function initEventStream() {
    const evtSource = new EventSource('/api/stream');

    evtSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updateDashboard(data);
        } catch (err) {
            console.error('Failed to parse SSE payload:', err);
        }
    };

    evtSource.onerror = () => {
        console.warn('SSE disconnected, falling back to polling...');
        evtSource.close();
        setTimeout(initEventStream, 3000);
    };
}

// Update all dashboard elements
function updateDashboard(data) {
    const p = data.portfolio;
    const opps = data.opportunities || [];
    const positions = data.open_positions || [];
    const status = data.status || {};

    // 1. Status Badges
    document.getElementById('db-badge').innerText = `DB: ${status.db_type || 'Connected'}`;
    document.getElementById('uptime-display').innerText = `${status.uptime || 0}s`;

    // 2. KPIs
    document.getElementById('kpi-equity').innerText = `$${p.total_equity.toFixed(2)}`;
    const retEl = document.getElementById('kpi-return-pct');
    retEl.innerText = `${p.return_pct >= 0 ? '+' : ''}${p.return_pct.toFixed(2)}%`;
    retEl.className = `text-xs font-medium mt-0.5 ${p.return_pct >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;

    const pnlEl = document.getElementById('kpi-pnl');
    pnlEl.innerText = `${p.total_pnl >= 0 ? '+' : ''}$${p.total_pnl.toFixed(2)}`;
    pnlEl.className = `text-2xl font-bold font-mono ${p.total_pnl >= 0 ? 'text-emerald-400' : 'text-rose-400'}`;

    document.getElementById('kpi-cash').innerText = `$${p.cash.toFixed(2)}`;
    document.getElementById('kpi-locked').innerText = `$${p.locked_capital.toFixed(2)}`;
    document.getElementById('kpi-events-count').innerText = status.events_count || 0;
    const tokensEl = document.getElementById('kpi-tokens-count');
    if (tokensEl && status.tokens_count !== undefined) {
        tokensEl.innerText = status.tokens_count;
    }
    document.getElementById('kpi-positions-count').innerText = p.open_positions_count;

    // 3. System Resources (CPU & RAM)
    const sys = data.system;
    if (sys) {
        const cpuEl = document.getElementById('cpu-display');
        if (cpuEl) cpuEl.innerText = `${sys.cpu_percent.toFixed(1)}%`;

        const ramEl = document.getElementById('ram-display');
        if (ramEl) ramEl.innerText = `${sys.ram_used_mb} MB`;

        const kpiCpu = document.getElementById('kpi-cpu');
        if (kpiCpu) kpiCpu.innerText = `${sys.cpu_percent.toFixed(1)}%`;

        const kpiRam = document.getElementById('kpi-ram');
        if (kpiRam) kpiRam.innerText = `${sys.ram_used_mb} MB (${sys.ram_percent}%)`;

        const kpiCpuBar = document.getElementById('kpi-cpu-bar');
        if (kpiCpuBar) kpiCpuBar.style.width = `${Math.min(100, Math.max(3, sys.cpu_percent))}%`;

        const kpiRamBar = document.getElementById('kpi-ram-bar');
        if (kpiRamBar) kpiRamBar.style.width = `${Math.min(100, Math.max(3, sys.ram_percent))}%`;
    }

    // Toggle button state
    const btn = document.getElementById('toggle-engine-btn');
    if (p.is_running) {
        btn.innerHTML = '<i class="fa-solid fa-pause"></i> <span>Pause</span>';
        btn.className = 'px-3 py-1.5 rounded-lg font-medium transition flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white';
    } else {
        btn.innerHTML = '<i class="fa-solid fa-play"></i> <span>Resume</span>';
        btn.className = 'px-3 py-1.5 rounded-lg font-medium transition flex items-center gap-1.5 bg-amber-600 hover:bg-amber-500 text-white';
    }

    // 3. Update Chart
    const now = new Date().toLocaleTimeString();
    if (labelsHistory.length > 40) {
        labelsHistory.shift();
        equityHistory.shift();
    }
    labelsHistory.push(now);
    equityHistory.push(p.total_equity);
    equityChart.update('none');

    // 4. Update Arbitrage Scanner Table
    renderOpportunities(opps, p.spread_threshold);

    // 5. Update Open Positions
    renderPositions(positions);
}

// Render Arbitrage Scanner Rows
function renderOpportunities(opps, threshold) {
    const tbody = document.getElementById('opps-table-body');
    if (!opps || opps.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="py-4 text-center text-slate-400 font-sans">Scanning live Polymarket order books...</td></tr>';
        return;
    }

    tbody.innerHTML = opps.map(op => {
        const isActionable = op.net_spread >= threshold;
        const spreadBadge = isActionable 
            ? `<span class="bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded text-[11px] font-bold">ACTIONABLE (+${(op.net_spread * 100).toFixed(2)}%)</span>`
            : `<span class="bg-slate-700/40 text-slate-400 px-2 py-0.5 rounded text-[11px]">WATCHING (${(op.net_spread * 100).toFixed(2)}%)</span>`;

        return `
            <tr class="hover:bg-slate-800/40 transition">
                <td class="py-2.5 px-3 font-sans font-medium text-slate-200">${op.event_title}</td>
                <td class="py-2.5 px-3 text-slate-400">${op.outcomes_count} mkts</td>
                <td class="py-2.5 px-3 font-bold text-slate-200">$${op.basket_sum.toFixed(3)}</td>
                <td class="py-2.5 px-3 text-slate-300">${(op.gross_spread * 100).toFixed(2)}%</td>
                <td class="py-2.5 px-3 font-bold ${op.net_spread > 0 ? 'text-emerald-400' : 'text-slate-400'}">${(op.net_spread * 100).toFixed(2)}%</td>
                <td class="py-2.5 px-3">${spreadBadge}</td>
                <td class="py-2.5 px-3 text-slate-500 text-[11px]">${op.created_at}</td>
            </tr>
        `;
    }).join('');
}

// Render Open Positions Rows
function renderPositions(positions) {
    const tbody = document.getElementById('positions-table-body');
    if (!positions || positions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="py-4 text-center text-slate-500 font-sans">No open positions currently.</td></tr>';
        return;
    }

    tbody.innerHTML = positions.map(p => `
        <tr class="hover:bg-slate-800/40 transition">
            <td class="py-2 px-3 font-sans text-slate-300 font-medium">${p.event_title}</td>
            <td class="py-2 px-3 font-mono">$${p.entry_basket.toFixed(3)}</td>
            <td class="py-2 px-3 font-mono">${p.shares}</td>
            <td class="py-2 px-3 font-mono">$${p.notional.toFixed(2)}</td>
            <td class="py-2 px-3"><span class="bg-indigo-500/20 text-indigo-400 border border-indigo-500/30 px-1.5 py-0.5 rounded text-[10px] font-bold">REBALANCING</span></td>
        </tr>
    `).join('');
}

// Fetch and load recent trade fills
async function loadTrades() {
    try {
        const res = await fetch('/api/trades?limit=15');
        const trades = await res.json();
        const tbody = document.getElementById('trades-table-body');
        if (!trades || trades.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" class="py-4 text-center text-slate-500 font-sans">No trades executed yet.</td></tr>';
            return;
        }

        tbody.innerHTML = trades.map(t => `
            <tr class="hover:bg-slate-800/40 transition">
                <td class="py-2 px-3 font-sans text-slate-300">${t.outcome_name || 'Outcome'}</td>
                <td class="py-2 px-3 font-bold text-emerald-400">${t.side}</td>
                <td class="py-2 px-3 font-mono">$${parseFloat(t.price).toFixed(3)}</td>
                <td class="py-2 px-3 font-mono">${parseFloat(t.shares).toFixed(1)}</td>
                <td class="py-2 px-3 font-mono text-slate-300">$${parseFloat(t.cost).toFixed(2)}</td>
            </tr>
        `).join('');
    } catch (e) {
        console.warn('Failed to fetch trades:', e);
    }
}

// Control Engine: Toggle Pause/Resume
async function toggleEngine() {
    const btn = document.getElementById('toggle-engine-btn');
    const isRunning = btn.innerText.includes('Pause');
    const action = isRunning ? 'pause' : 'resume';

    try {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action })
        });
    } catch (e) {
        alert('Failed to control engine: ' + e);
    }
}

// Control Engine: Reset Account
async function resetAccount() {
    if (!confirm('Reset simulation portfolio back to $50.00?')) return;
    try {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'reset' })
        });
        equityHistory.length = 0;
        labelsHistory.length = 0;
        loadTrades();
    } catch (e) {
        alert('Failed to reset account: ' + e);
    }
}

// Update Min Net Spread Threshold
async function updateThreshold(val) {
    const rate = parseFloat(val) / 100.0;
    document.getElementById('threshold-val').innerText = `${parseFloat(val).toFixed(2)}%`;
    try {
        await fetch('/api/control', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'set_threshold', value: rate })
        });
    } catch (e) {
        console.warn('Failed to update threshold:', e);
    }
}

// Interactive SQL Query Runner
function setSampleQuery(q) {
    document.getElementById('sql-input').value = q;
}

async function executeSQLQuery() {
    const query = document.getElementById('sql-input').value.trim();
    const metaEl = document.getElementById('query-meta');
    const container = document.getElementById('query-results-container');
    const errorEl = document.getElementById('query-error-container');
    const thead = document.getElementById('query-results-head');
    const tbody = document.getElementById('query-results-body');

    metaEl.innerText = 'Executing query...';
    errorEl.classList.add('hidden');
    container.classList.add('hidden');

    try {
        const res = await fetch('/api/query', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query })
        });

        const data = await res.json();
        if (!res.ok || !data.success) {
            errorEl.innerText = `Error: ${data.detail || data.error || 'Query failed'}`;
            errorEl.classList.remove('hidden');
            metaEl.innerText = '';
            return;
        }

        metaEl.innerText = `Rows: ${data.row_count} | Execution Time: ${data.execution_time_ms}ms`;

        if (data.columns.length === 0) {
            tbody.innerHTML = '<tr><td class="p-3 text-slate-400 font-sans">Query returned zero rows.</td></tr>';
            container.classList.remove('hidden');
            return;
        }

        // Render Head
        thead.innerHTML = `<tr>${data.columns.map(c => `<th class="py-2 px-3">${c}</th>`).join('')}</tr>`;

        // Render Rows
        tbody.innerHTML = data.rows.map(row => {
            return `<tr class="hover:bg-slate-800/50">${data.columns.map(col => {
                const val = row[col];
                const displayVal = (val === null || val === undefined) ? '<span class="text-slate-500">NULL</span>' : val;
                return `<td class="py-2 px-3 border-r border-darkBorder/40 last:border-0">${displayVal}</td>`;
            }).join('')}</tr>`;
        }).join('');

        container.classList.remove('hidden');
    } catch (e) {
        errorEl.innerText = `Network/Server Error: ${e}`;
        errorEl.classList.remove('hidden');
        metaEl.innerText = '';
    }
}
