/**
 * Quantix HFT Engine Frontend Controller
 * Manages WebSocket telemetry stream, real-time Chart.js equity updates,
 * order book depth ladder rendering, and trading controls.
 */

document.addEventListener("DOMContentLoaded", () => {
    // DOM Elements
    const equityVal = document.getElementById("equity-val");
    const pnlVal = document.getElementById("pnl-val");
    const rotationCountdownVal = document.getElementById("rotation-countdown-val");
    const pairPnlVal = document.getElementById("pair-pnl-val");
    const pairStatusBadge = document.getElementById("pair-status-badge");
    const inventoryVal = document.getElementById("inventory-val");
    const fillsVal = document.getElementById("fills-val");
    const feesVal = document.getElementById("fees-val");

    const statusIndicator = document.getElementById("status-indicator");
    const statusText = document.getElementById("status-text");
    const modeBadge = document.getElementById("mode-badge");

    const symbolDisplay = document.getElementById("symbol-display");
    const midDisplay = document.getElementById("mid-display");
    const spreadDisplay = document.getElementById("spread-display");
    const spreadLadderText = document.getElementById("spread-ladder-text");

    const ofiBar = document.getElementById("ofi-bar");
    const ofiReading = document.getElementById("ofi-reading");
    const volReading = document.getElementById("vol-reading");

    const asksContainer = document.getElementById("asks-container");
    const bidsContainer = document.getElementById("bids-container");
    const ourAskCallout = document.getElementById("our-ask-callout");
    const ourBidCallout = document.getElementById("our-bid-callout");
    const ourAskPrice = document.getElementById("our-ask-price");
    const ourBidPrice = document.getElementById("our-bid-price");
    const ourAskNotional = document.getElementById("our-ask-notional");
    const ourBidNotional = document.getElementById("our-bid-notional");
    const dynamicSizingToggle = document.getElementById("dynamic-sizing-toggle");

    const fillsTbody = document.getElementById("fills-tbody");
    const fillsCountBadge = document.getElementById("fills-count-badge");
    const rotationsTbody = document.getElementById("rotations-tbody");
    const rotationsCountBadge = document.getElementById("rotations-count-badge");

    const btnStart = document.getElementById("btn-start");
    const btnStop = document.getElementById("btn-stop");
    const btnReset = document.getElementById("btn-reset");
    const btnScan = document.getElementById("btn-scan");
    const btnRotate = document.getElementById("btn-rotate");
    const btnTheme = document.getElementById("btn-theme");
    const btnExport = document.getElementById("btn-export");

    const coinSelect = document.getElementById("coin-select");
    const autoRotateToggle = document.getElementById("auto-rotate-toggle");
    const orderSizeInput = document.getElementById("order-size-input");
    const gammaInput = document.getElementById("gamma-input");
    const gammaVal = document.getElementById("gamma-val");
    const betaInput = document.getElementById("beta-input");
    const betaVal = document.getElementById("beta-val");
    const spreadInput = document.getElementById("spread-input");
    const spreadVal = document.getElementById("spread-val");
    const marketSpreadInput = document.getElementById("market-spread-input");
    const marketSpreadVal = document.getElementById("market-spread-val");
    const maxInvInput = document.getElementById("max-inv-input");
    const circuitBreakerBadge = document.getElementById("circuit-breaker-badge");

    // Theme Management (Light mode is default)
    const savedTheme = localStorage.getItem("quantix_theme") || "light";
    if (savedTheme === "dark") {
        document.body.classList.add("dark-theme");
        if (btnTheme) btnTheme.textContent = "☀️ Light";
    } else {
        document.body.classList.remove("dark-theme");
        if (btnTheme) btnTheme.textContent = "🌙 Dark";
    }

    const isCurrentDark = () => document.body.classList.contains("dark-theme");

    // Sliders event listeners
    gammaInput.addEventListener("input", (e) => gammaVal.textContent = parseFloat(e.target.value).toFixed(2));
    betaInput.addEventListener("input", (e) => betaVal.textContent = parseFloat(e.target.value).toFixed(2));
    spreadInput.addEventListener("input", (e) => spreadVal.textContent = parseFloat(e.target.value).toFixed(1) + " bps");
    if (marketSpreadInput) {
        marketSpreadInput.addEventListener("input", (e) => marketSpreadVal.textContent = parseFloat(e.target.value).toFixed(1) + " bps");
    }

    // Initialize Chart.js
    const ctx = document.getElementById("equity-chart").getContext("2d");
    const equityChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: [],
            datasets: [{
                label: "Equity ($)",
                data: [],
                borderColor: "#10b981",
                backgroundColor: "rgba(16, 185, 129, 0.08)",
                borderWidth: 2,
                fill: true,
                tension: 0.1,
                pointRadius: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                x: {
                    display: false
                },
                y: {
                    grid: { color: isCurrentDark() ? "#212b3d" : "#e2e8f0" },
                    ticks: {
                        color: isCurrentDark() ? "#8b949e" : "#64748b",
                        font: { family: "monospace", size: 10 },
                        callback: (v) => "$" + v.toFixed(2)
                    }
                }
            }
        }
    });

    function updateChartTheme() {
        const dark = isCurrentDark();
        equityChart.options.scales.y.grid.color = dark ? "#212b3d" : "#e2e8f0";
        equityChart.options.scales.y.ticks.color = dark ? "#8b949e" : "#64748b";
        equityChart.update("none");
    }

    // WebSocket Connection
    let ws = null;
    let reconnectTimeout = null;

    function connectWebSocket() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsUrl = `${protocol}//${window.location.host}/ws/live`;

        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            console.log("[WS] Connected to live telemetry feed.");
            if (reconnectTimeout) clearTimeout(reconnectTimeout);
        };

        ws.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                renderTelemetry(data);
            } catch (err) {
                console.error("[WS] Parse error:", err);
            }
        };

        ws.onclose = () => {
            console.warn("[WS] Feed disconnected. Retrying in 2s...");
            reconnectTimeout = setTimeout(connectWebSocket, 2000);
        };

        ws.onerror = (err) => {
            console.error("[WS] Error:", err);
            ws.close();
        };
    }

    function renderTelemetry(data) {
        // Status & Mode
        const isRunning = (data.status === "RUNNING");
        statusIndicator.className = "status-indicator " + (isRunning ? "running" : "stopped");
        statusText.textContent = data.status;
        btnStart.disabled = isRunning;
        btnStop.disabled = !isRunning;

        if (circuitBreakerBadge) {
            if (data.circuit_breaker_active) {
                circuitBreakerBadge.style.display = "inline-block";
                circuitBreakerBadge.textContent = "PULLED: " + (data.circuit_breaker_reason || "PROTECTIVE HALT");
            } else {
                circuitBreakerBadge.style.display = "none";
            }
        }

        // Metrics
        equityVal.textContent = `$${data.equity.toFixed(2)}`;

        const pnl = data.net_pnl;
        const pnlPct = data.return_pct;
        const sign = pnl >= 0 ? "+" : "";
        pnlVal.textContent = `${sign}$${pnl.toFixed(2)} (${sign}${pnlPct.toFixed(2)}%)`;
        pnlVal.className = "tile-value " + (pnl > 0 ? "pnl-positive" : (pnl < 0 ? "pnl-negative" : "pnl-neutral"));

        // Rotation Countdown & Pair Telemetry
        if (rotationCountdownVal && data.rotation_countdown_str) {
            rotationCountdownVal.textContent = data.rotation_countdown_str;
        }

        if (pairPnlVal) {
            const pPnl = data.pair_pnl || 0.0;
            const pSign = pPnl >= 0 ? "+" : "";
            pairPnlVal.textContent = `${pSign}$${pPnl.toFixed(2)}`;
            pairPnlVal.className = "tile-value " + (pPnl > 0 ? "pnl-positive" : (pPnl < 0 ? "pnl-negative" : "pnl-neutral"));
        }

        if (pairStatusBadge) {
            if (data.pair_status === "FLATTENING") {
                pairStatusBadge.textContent = "OFFLOADING INVENTORY...";
                pairStatusBadge.style.background = "#d97706";
            } else if (data.pair_status === "SWITCHING") {
                pairStatusBadge.textContent = "ROTATING TO BEST PAIR...";
                pairStatusBadge.style.background = "#7c3aed";
            } else {
                pairStatusBadge.textContent = `PAIR: ${data.coin || "ACTIVE"}`;
                pairStatusBadge.style.background = "#0284c7";
            }
        }

        inventoryVal.textContent = `${data.inventory.toFixed(2)} (${sign}$${data.inventory_usd.toFixed(2)})`;
        fillsVal.textContent = data.fills_count;
        feesVal.textContent = `$${data.total_fees.toFixed(4)}`;

        // Market Info
        symbolDisplay.textContent = data.coin;
        midDisplay.textContent = `$${data.mid_price.toFixed(4)}`;
        spreadDisplay.textContent = `${data.spread_bps.toFixed(2)} bps`;
        spreadLadderText.textContent = `SPREAD: ${data.spread_bps.toFixed(2)} bps`;

        // Microstructure Gauges
        const ofi = data.ofi;
        ofiReading.textContent = ofi.toFixed(1);
        const clampedOFI = Math.max(-50, Math.min(50, ofi));
        const ofiPct = Math.abs(clampedOFI);

        if (clampedOFI >= 0) {
            ofiBar.style.left = "50%";
            ofiBar.style.width = `${ofiPct}%`;
            ofiBar.style.backgroundColor = "#10b981";
        } else {
            ofiBar.style.left = `${50 - ofiPct}%`;
            ofiBar.style.width = `${ofiPct}%`;
            ofiBar.style.backgroundColor = "#ef4444";
        }

        volReading.textContent = `${data.volatility.toFixed(1)} bps`;

        // Active Quotes Display
        if (data.active_ask) {
            ourAskCallout.style.display = "flex";
            ourAskPrice.textContent = `$${data.active_ask.toFixed(4)}`;
            if (ourAskNotional && data.our_ask_usd) {
                ourAskNotional.textContent = `$${data.our_ask_usd.toFixed(2)} Notional`;
            }
        } else {
            ourAskCallout.style.display = "none";
        }

        if (data.active_bid) {
            ourBidCallout.style.display = "flex";
            ourBidPrice.textContent = `$${data.active_bid.toFixed(4)}`;
            if (ourBidNotional && data.our_bid_usd) {
                ourBidNotional.textContent = `$${data.our_bid_usd.toFixed(2)} Notional`;
            }
        } else {
            ourBidCallout.style.display = "none";
        }

        // Render Depth Ladder
        if (data.book_depth && data.book_depth.asks) {
            // Render Asks (descending top to bottom towards spread)
            const asks = data.book_depth.asks.slice().reverse();
            asksContainer.innerHTML = asks.map(a => `
                <div class="depth-row">
                    <span>${a.px.toFixed(4)}</span>
                    <span>${a.sz.toFixed(1)}</span>
                    <span>$${(a.px * a.sz).toFixed(0)}</span>
                </div>
            `).join("");

            // Render Bids (highest bid first)
            const bids = data.book_depth.bids;
            bidsContainer.innerHTML = bids.map(b => `
                <div class="depth-row">
                    <span>${b.px.toFixed(4)}</span>
                    <span>${b.sz.toFixed(1)}</span>
                    <span>$${(b.px * b.sz).toFixed(0)}</span>
                </div>
            `).join("");
        }

        // Render Chart
        if (data.equity_history && data.equity_history.length > 0) {
            const labels = data.equity_history.map(h => new Date(h.time * 1000).toLocaleTimeString());
            const points = data.equity_history.map(h => h.equity);

            equityChart.data.labels = labels;
            equityChart.data.datasets[0].data = points;

            // Dynamic color based on PnL
            if (pnl >= 0) {
                equityChart.data.datasets[0].borderColor = "#10b981";
                equityChart.data.datasets[0].backgroundColor = "rgba(16, 185, 129, 0.08)";
            } else {
                equityChart.data.datasets[0].borderColor = "#ef4444";
                equityChart.data.datasets[0].backgroundColor = "rgba(239, 68, 68, 0.08)";
            }

            equityChart.update("none");
        }

        // Render Fills Table
        fillsCountBadge.textContent = `${data.fills_count} Fills`;
        if (data.recent_fills && data.recent_fills.length > 0) {
            fillsTbody.innerHTML = data.recent_fills.map(f => `
                <tr>
                    <td>${f.time}</td>
                    <td><span class="${f.side.includes('BUY') ? 'badge-buy' : 'badge-sell'}">${f.side}</span></td>
                    <td>$${f.price.toFixed(4)}</td>
                    <td>${f.size.toFixed(2)}</td>
                    <td>$${f.notional.toFixed(2)}</td>
                    <td>$${f.fee.toFixed(4)}</td>
                </tr>
            `).join("");
        }

        // Render Rotations Table
        if (rotationsTbody && data.rotation_history) {
            rotationsCountBadge.textContent = `${data.rotation_history.length} Rotations`;
            if (data.rotation_history.length > 0) {
                rotationsTbody.innerHTML = data.rotation_history.map(r => `
                    <tr>
                        <td>${r.time}</td>
                        <td><strong>${r.from_coin}</strong></td>
                        <td><span class="badge-buy" style="background: rgba(2, 132, 199, 0.15); color: #0284c7;">${r.to_coin}</span></td>
                        <td>${r.duration_min}m</td>
                        <td class="${r.pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}">${r.pnl >= 0 ? '+' : ''}$${r.pnl.toFixed(2)} (${r.return_pct >= 0 ? '+' : ''}${r.return_pct.toFixed(1)}%)</td>
                        <td>${r.fills}</td>
                        <td style="font-size: 11px; color: var(--text-muted);">${r.reason || '--'}</td>
                    </tr>
                `).join("");
            }
        }
    }

    // Action Handlers
    btnStart.addEventListener("click", async () => {
        const payload = {
            coin: coinSelect.value,
            initial_capital: 50.0,
            order_size_usd: Math.max(10.0, parseFloat(orderSizeInput.value) || 10.0),
            min_order_size_usd: 10.0,
            dynamic_sizing: dynamicSizingToggle ? dynamicSizingToggle.checked : true,
            gamma: parseFloat(gammaInput.value),
            beta_ofi: parseFloat(betaInput.value),
            min_spread_bps: parseFloat(spreadInput.value),
            min_market_spread_bps: marketSpreadInput ? parseFloat(marketSpreadInput.value) : 4.5,
            max_inventory_usd: Math.max(10.0, parseFloat(maxInvInput.value) || 30.0),
            auto_rotate: autoRotateToggle ? autoRotateToggle.checked : true,
            rotation_interval_min: 15.0,
            mode: "SIMULATED"
        };

        btnStart.disabled = true;
        try {
            const res = await fetch("/api/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
            const data = await res.json();
            console.log("Start response:", data);
        } catch (err) {
            alert("Error starting engine: " + err.message);
            btnStart.disabled = false;
        }
    });

    btnStop.addEventListener("click", async () => {
        btnStop.disabled = true;
        try {
            await fetch("/api/stop", { method: "POST" });
        } catch (err) {
            alert("Error stopping engine: " + err.message);
        }
    });

    btnReset.addEventListener("click", async () => {
        if (confirm("Reset account balance to $50.00 and clear history?")) {
            await fetch("/api/reset", { method: "POST" });
        }
    });

    if (btnRotate) {
        btnRotate.addEventListener("click", async () => {
            btnRotate.textContent = "Offloading...";
            btnRotate.disabled = true;
            try {
                const res = await fetch("/api/rotate", { method: "POST" });
                const d = await res.json();
                console.log("Rotate response:", d);
            } catch (err) {
                alert("Error initiating rotation: " + err.message);
            } finally {
                setTimeout(() => {
                    btnRotate.textContent = "🔄 Rotate Pair";
                    btnRotate.disabled = false;
                }, 2000);
            }
        });
    }

    btnScan.addEventListener("click", async () => {
        btnScan.textContent = "Scanning...";
        btnScan.disabled = true;
        try {
            const res = await fetch("/api/screener");
            const data = await res.json();
            if (data.candidates && data.candidates.length > 0) {
                coinSelect.innerHTML = data.candidates.slice(0, 10).map(c => `
                    <option value="${c.name}">${c.name} (${c.spread_bps} bps spread / $${(c.vol_24h / 1e6).toFixed(1)}M vol)</option>
                `).join("");
                alert(`Screener complete! Found ${data.candidates.length} pairs. Top pair selected.`);
            }
        } catch (err) {
            alert("Screener failed: " + err.message);
        } finally {
            btnScan.textContent = "⚡ Scan Pairs";
            btnScan.disabled = false;
        }
    });

    // Theme Toggle Handler
    if (btnTheme) {
        btnTheme.addEventListener("click", () => {
            const isDark = document.body.classList.toggle("dark-theme");
            btnTheme.textContent = isDark ? "☀️ Light" : "🌙 Dark";
            localStorage.setItem("quantix_theme", isDark ? "dark" : "light");
            updateChartTheme();
        });
    }

    // Export CSV Handler
    if (btnExport) {
        btnExport.addEventListener("click", () => {
            window.location.href = "/api/history/export";
        });
    }

    // Start WebSocket
    connectWebSocket();
});
