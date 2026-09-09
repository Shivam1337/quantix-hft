import { useEffect, useState } from "react";
import { getExchanges, getFundingHistory, getOpportunities } from "../api";
import { CoinCrossExchangeView } from "../components/CoinCrossExchangeView";
import { FundingHistoryChart } from "../components/FundingHistoryChart";
import { MetricCard } from "../components/MetricCard";
import type { ExchangeMarket, ExchangeSummary, FundingSettlement, Opportunity } from "../types";

type Props = {
  venue: string;
  symbol: string;
  onBack: () => void;
  onNavigate?: (to: string) => void;
  onSelectSimulate?: (opp: Opportunity) => void;
  onExecute?: (opp: Opportunity) => void;
};

export function CoinDetailsPage({ venue, symbol, onBack, onSelectSimulate, onExecute }: Props) {
  const [market, setMarket] = useState<ExchangeMarket | null>(null);
  const [history, setHistory] = useState<FundingSettlement[]>([]);
  const [exchanges, setExchanges] = useState<ExchangeSummary[]>([]);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [selectedVenue, setSelectedVenue] = useState(venue || "hyperliquid");
  const [viewMode, setViewMode] = useState<"single" | "cross">("single");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const venueId = selectedVenue.toLowerCase();
  const coinSymbol = (symbol || "BTC-PERP").toUpperCase();

  useEffect(() => {
    let active = true;
    setLoading(true);
    Promise.all([
      getFundingHistory(venueId, coinSymbol, 300),
      getExchanges(),
      getOpportunities(),
    ])
      .then(([records, exList, oppList]) => {
        if (!active) return;
        setHistory(records);
        setExchanges(exList);
        setOpportunities(oppList);
        const currentEx = exList.find((e) => e.id.toLowerCase() === venueId);
        const currentMkt = currentEx?.markets.find((m) => m.symbol.toUpperCase() === coinSymbol);
        setMarket(currentMkt ?? null);
        setError("");
      })
      .catch((err) => {
        if (active) setError(err instanceof Error ? err.message : "Failed to load coin data");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [venueId, coinSymbol]);

  const latestRate = market?.funding_rate ?? history[0]?.funding_rate ?? null;
  const latestPrice = market?.mark_price ?? 0;
  const latestOi = market?.open_interest ?? 0;
  const apr = latestRate === null ? null : latestRate * 24 * 365 * 100;
  const venueTitle = venueId.charAt(0).toUpperCase() + venueId.slice(1);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button onClick={onBack} className="button flex items-center gap-2 px-4 py-2 text-sm font-medium">
          <span>←</span> Back to Exchanges Directory
        </button>
        <div className="flex rounded-lg border border-slate-800 bg-slate-900/60 p-0.5 text-xs">
          <button
            onClick={() => setViewMode("single")}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              viewMode === "single" ? "bg-cyan text-slate-950 font-bold" : "text-slate-400 hover:text-white"
            }`}
          >
            Single Venue ({venueTitle})
          </button>
          <button
            onClick={() => setViewMode("cross")}
            className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
              viewMode === "cross" ? "bg-cyan text-slate-950 font-bold" : "text-slate-400 hover:text-white"
            }`}
          >
            Cross-Exchange Arbitrage & Rates
          </button>
        </div>
      </div>

      {error && <div className="alert">{error}</div>}

      {viewMode === "cross" ? (
        <CoinCrossExchangeView
          symbol={coinSymbol}
          exchanges={exchanges}
          opportunities={opportunities}
          onSelectSimulate={onSelectSimulate}
          onSelectVenue={(v) => {
            setSelectedVenue(v);
            setViewMode("single");
          }}
        />
      ) : (
        <>
          <div className="panel p-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-cyan/10 font-mono text-lg font-bold text-cyan border border-cyan/30">
                  {coinSymbol.split("-")[0]}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-2xl font-bold text-white">{coinSymbol}</h2>
                    <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs font-semibold text-slate-300 border border-slate-700">
                      {venueTitle}
                    </span>
                  </div>
                  <p className="text-xs text-slate-400">
                    Perpetual contract yield & historical funding cycle analysis
                  </p>
                </div>
              </div>

              <div className="text-right">
                <p className="text-[10px] uppercase text-slate-500 font-medium">Annualized APR</p>
                <p className={`font-mono text-2xl font-bold ${apr === null ? "text-slate-500" : apr >= 0 ? "text-cyan" : "text-rose-400"}`}>
                  {apr === null ? "—" : `${apr >= 0 ? "+" : ""}${apr.toFixed(2)}%`}
                </p>
              </div>
            </div>

            <div className="mt-6 grid grid-cols-2 gap-4 md:grid-cols-4">
              <MetricCard
                label="Mark Price"
                value={`$${latestPrice.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
                detail="live index price"
                accent="white"
              />
              <MetricCard
                label="Confirmed Funding"
                value={latestRate === null ? "—" : `${latestRate >= 0 ? "+" : ""}${(latestRate * 100).toFixed(4)}%`}
                detail="latest exchange settlement"
                accent={latestRate === null ? "white" : latestRate >= 0 ? "cyan" : "amber"}
              />
              <MetricCard
                label="Open Interest"
                value={`$${(latestOi / 1_000_000).toFixed(2)}M`}
                detail={`${latestOi > 0 ? "healthy liquidity" : "low volume"}`}
              />
              <MetricCard
                label="Interval Window"
                value={market?.funding_rate === null || market?.funding_rate === undefined ? "—" : `${market.funding_interval_hours} Hour`}
                detail="confirmed settlement window"
                accent="white"
              />
            </div>
          </div>

          {loading ? (
            <div className="panel p-8 text-center text-slate-400">Loading coin historical records…</div>
          ) : (
            <>
              <FundingHistoryChart records={history} symbol={coinSymbol} venue={venueId} />

              <div className="panel overflow-hidden">
                <div className="section-heading">
                  <div>
                    <p className="eyebrow text-cyan">Database Records</p>
                    <h2>Settled Funding Rate Events ({history.length} cycles)</h2>
                  </div>
                </div>
                <div className="max-h-80 overflow-y-auto">
                  <table>
                    <thead className="sticky top-0 bg-slate-900">
                      <tr><th>Funding Cycle (UTC)</th><th>Confirmed Rate / h</th><th>Cycle Window</th><th>Settled At</th><th>Source</th></tr>
                    </thead>
                    <tbody>
                      {history.map((rec) => (
                        <tr key={rec.id}>
                          <td className="font-mono font-medium text-white">
                            {rec.funding_cycle_at
                              ? new Date(rec.funding_cycle_at).toLocaleString()
                            : new Date(rec.settled_at).toLocaleString()}
                          </td>
                          <td className={`font-mono font-medium ${rec.funding_rate >= 0 ? "text-emerald-300" : "text-rose-400"}`}>
                            {(rec.funding_rate * 100).toFixed(4)}%
                          </td>
                          <td><span className="tag receive">{rec.funding_interval_hours}h</span></td>
                          <td className="font-mono text-xs text-slate-400">{new Date(rec.settled_at).toLocaleString()}</td>
                          <td className="font-mono text-xs text-slate-400">{rec.source}</td>
                        </tr>
                      ))}
                      {!history.length && (
                        <tr>
                          <td colSpan={5} className="empty-state">No exchange-confirmed settlements found yet.</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
