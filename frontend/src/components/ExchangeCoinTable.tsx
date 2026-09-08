import { useState } from "react";
import type { ExchangeMarket } from "../types";

type Props = {
  markets: ExchangeMarket[];
  venue: string;
  selectedSymbol: string | null;
  onSelectSymbol: (symbol: string) => void;
};

export function ExchangeCoinTable({ markets, venue, selectedSymbol, onSelectSymbol }: Props) {
  const [filter, setFilter] = useState("");

  const filtered = markets.filter((m) =>
    m.symbol.toLowerCase().includes(filter.toLowerCase())
  );

  return (
    <div className="panel overflow-hidden">
      <div className="section-heading">
        <div>
          <p className="eyebrow text-cyan">{venue.toUpperCase()} Markets</p>
          <h2>Available Coins & Live Rates</h2>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="text"
            placeholder="Search coin..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="field w-40 text-xs py-1.5"
          />
          <span className="count-badge">{filtered.length} coins</span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Current Rate / h</th>
              <th>Annualized APR</th>
              <th>Mark Price</th>
              <th>Bid / Ask</th>
              <th>Open Interest</th>
              <th>Interval</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((item) => {
              const isSelected = selectedSymbol === item.symbol;
              const apr = item.funding_rate * 24 * 365 * 100;
              const spreadBps = item.mark_price > 0 ? ((item.ask - item.bid) / item.mark_price) * 10_000 : 0;

              return (
                <tr
                  key={item.symbol}
                  className={`cursor-pointer transition hover:bg-slate-900/60 ${
                    isSelected ? "bg-cyan/10 border-l-2 border-cyan" : ""
                  }`}
                  onClick={() => onSelectSymbol(item.symbol)}
                >
                  <td>
                    <strong>{item.symbol}</strong>
                    <div className="text-[10px] text-slate-500">
                      Observed: {new Date(item.observed_at).toLocaleTimeString()}
                    </div>
                  </td>
                  <td>
                    <span
                      className={`font-mono font-medium ${
                        item.funding_rate >= 0 ? "text-emerald-300" : "text-rose-400"
                      }`}
                    >
                      {item.funding_rate >= 0 ? "+" : ""}
                      {(item.funding_rate * 100).toFixed(4)}%
                    </span>
                  </td>
                  <td className="font-mono text-cyan font-semibold">
                    {apr >= 0 ? "+" : ""}
                    {apr.toFixed(1)}%
                  </td>
                  <td className="font-mono text-slate-200">
                    ${item.mark_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                  </td>
                  <td className="font-mono text-xs text-slate-400">
                    ${item.bid.toFixed(1)} / ${item.ask.toFixed(1)}
                    <span className="ml-1 text-[10px] text-slate-500">({spreadBps.toFixed(1)} bps)</span>
                  </td>
                  <td className="font-mono text-slate-300">
                    ${item.open_interest.toLocaleString()}
                  </td>
                  <td>
                    <span className="tag receive">{item.funding_interval_hours}h</span>
                  </td>
                  <td>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onSelectSymbol(item.symbol);
                      }}
                      className="button button-primary text-xs"
                    >
                      View Details →
                    </button>
                  </td>
                </tr>
              );
            })}
            {!filtered.length && (
              <tr>
                <td colSpan={8} className="empty-state">
                  No coins found matching "{filter}".
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
