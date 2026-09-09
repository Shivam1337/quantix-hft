import { useEffect, useState } from "react";
import { getExchanges } from "../api";
import type { ExchangeSummary } from "../types";
import { ExchangeCoinTable } from "./ExchangeCoinTable";

type Props = {
  onSelectCoin: (symbol: string, venue: string) => void;
};

export function ExchangeView({ onSelectCoin }: Props) {
  const [exchanges, setExchanges] = useState<ExchangeSummary[]>([]);
  const [selectedExchangeId, setSelectedExchangeId] = useState<string>("hyperliquid");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadExchanges = async () => {
    try {
      const data = await getExchanges();
      setExchanges(data);
      if (data.length > 0 && !data.some((e) => e.id === selectedExchangeId)) {
        setSelectedExchangeId(data[0].id);
      }
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load exchanges");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadExchanges();
  }, []);

  const currentExchange = exchanges.find((e) => e.id === selectedExchangeId) ?? exchanges[0];

  if (loading) {
    return <div className="panel p-8 text-center text-slate-400">Loading configured exchanges…</div>;
  }

  return (
    <div className="space-y-6">
      {error && <div className="alert">{error}</div>}

      {/* Configured Exchanges Cards */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {exchanges.map((ex) => {
          const isSelected = ex.id === selectedExchangeId;
          const confirmedRates = ex.markets
            .map((market) => market.funding_rate)
            .filter((rate): rate is number => rate !== null);
          const avgRate = confirmedRates.length
            ? confirmedRates.reduce((sum, rate) => sum + rate, 0) / confirmedRates.length
            : null;
          const avgApr = avgRate === null ? null : avgRate * 24 * 365 * 100;

          return (
            <div
              key={ex.id}
              onClick={() => setSelectedExchangeId(ex.id)}
              className={`cursor-pointer rounded-2xl border p-5 transition ${
                isSelected
                  ? "border-cyan bg-cyan/10 shadow-[0_0_20px_rgba(34,211,238,0.15)]"
                  : "panel hover:border-slate-600 hover:shadow-sm"
              }`}
            >
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold text-white">{ex.name}</h3>
                <span className={`status-dot text-[10px] ${ex.status === "active" ? "text-emerald-300" : "text-amber-400"}`}>
                  {ex.status.toUpperCase()}
                </span>
              </div>
              <div className="mt-4 flex items-baseline justify-between">
                <div>
                  <div className="text-[10px] uppercase text-slate-500">Tracked Coins</div>
                  <div className="font-mono text-xl font-bold text-slate-100">{ex.markets_count}</div>
                </div>
                <div className="text-right">
                  <div className="text-[10px] uppercase text-slate-500">Avg Confirmed APR</div>
                  <div className={`font-mono text-sm font-semibold ${avgApr === null ? "text-slate-500" : avgApr >= 0 ? "text-cyan" : "text-rose-400"}`}>
                    {avgApr === null ? "—" : `${avgApr >= 0 ? "+" : ""}${avgApr.toFixed(1)}%`}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Available Coins Table: clicking coin navigates to dedicated coin details page */}
      {currentExchange && (
        <ExchangeCoinTable
          markets={currentExchange.markets}
          venue={currentExchange.name}
          selectedSymbol={null}
          onSelectSymbol={(symbol) => onSelectCoin(symbol, currentExchange.id)}
        />
      )}
    </div>
  );
}
