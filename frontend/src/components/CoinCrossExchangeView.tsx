import { useMemo } from "react";
import type { ExchangeSummary, Opportunity } from "../types";

type Props = {
  symbol: string;
  exchanges: ExchangeSummary[];
  opportunities: Opportunity[];
  onSelectSimulate?: (opp: Opportunity) => void;
  onExecute?: (opp: Opportunity) => void;
  onSelectVenue?: (venue: string) => void;
};

export function CoinCrossExchangeView({
  symbol,
  exchanges,
  opportunities,
  onSelectSimulate,
  onSelectVenue,
}: Props) {
  const normSym = symbol.toUpperCase();
  const venueMarkets = useMemo(
    () =>
      exchanges
        .map((exchange) => {
          const market = exchange.markets.find((item) => item.symbol.toUpperCase() === normSym);
          return market ? { exchange, market } : null;
        })
        .filter((item): item is NonNullable<typeof item> => item !== null),
    [exchanges, normSym]
  );
  const coinArbs = opportunities.filter((opportunity) => opportunity.symbol.toUpperCase() === normSym);
  const bestArb = coinArbs[0];
  const confirmedRates = venueMarkets
    .map(({ market }) => market.funding_rate)
    .filter((rate): rate is number => rate !== null);
  const minRate = confirmedRates.length ? Math.min(...confirmedRates) : null;
  const maxRate = confirmedRates.length ? Math.max(...confirmedRates) : null;
  const prices = venueMarkets.map(({ market }) => market.mark_price);
  const minPrice = prices.length ? Math.min(...prices) : 0;
  const maxPrice = prices.length ? Math.max(...prices) : 0;
  const priceSpreadBps = minPrice > 0 ? ((maxPrice - minPrice) / minPrice) * 10_000 : 0;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-4">
        <div className="panel p-4">
          <p className="eyebrow text-slate-400">Tracked Exchanges</p>
          <p className="mt-2 font-mono text-2xl font-bold text-white">
            {venueMarkets.length} <span className="text-xs font-normal text-slate-400">venues</span>
          </p>
          <p className="mt-1 text-xs text-slate-500">Live liquidity for {normSym}</p>
        </div>
        <div className="panel p-4">
          <p className="eyebrow text-cyan">Historical Opportunity APR</p>
          <p className="mt-2 font-mono text-2xl font-bold text-emerald-400">
            {bestArb ? `${bestArb.net_apr_pct >= 0 ? "+" : ""}${bestArb.net_apr_pct.toFixed(2)}%` : "—"}
          </p>
          <p className="mt-1 truncate text-xs text-slate-400">
            {bestArb ? `${bestArb.long_venue} / ${bestArb.short_venue}` : "No aligned confirmed history"}
          </p>
        </div>
        <div className="panel p-4">
          <p className="eyebrow text-amber">Confirmed Rate Divergence</p>
          <p className="mt-2 font-mono text-2xl font-bold text-amber">
            {minRate === null || maxRate === null ? "—" : `${((maxRate - minRate) * 100).toFixed(4)}%`}
            <span className="text-xs font-normal"> / h</span>
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {minRate === null || maxRate === null ? "Awaiting exchange confirmation" : `${((maxRate - minRate) * 24 * 365 * 100).toFixed(1)}% annual delta`}
          </p>
        </div>
        <div className="panel p-4">
          <p className="eyebrow text-purple-400">Price Basis Spread</p>
          <p className="mt-2 font-mono text-2xl font-bold text-purple-300">
            {priceSpreadBps.toFixed(1)} <span className="text-xs font-normal">bps</span>
          </p>
          <p className="mt-1 text-xs text-slate-500">${(maxPrice - minPrice).toFixed(2)} cross-venue gap</p>
        </div>
      </div>

      <div className="panel overflow-hidden">
        <div className="section-heading">
          <div><p className="eyebrow text-cyan">Confirmed Exchange Rates</p><h2>{normSym} Historical Rates & Pricing Across Venues</h2></div>
          <span className="count-badge">{venueMarkets.length} active exchanges</span>
        </div>
        <div className="overflow-x-auto">
          <table>
            <thead><tr><th>Exchange</th><th>Confirmed Rate / h</th><th>Annualized APR</th><th>Mark Price</th><th>Bid / Ask</th><th>Open Interest</th><th>Cycle</th><th>Action</th></tr></thead>
            <tbody>
              {venueMarkets.map(({ exchange, market }) => {
                const rate = market.funding_rate;
                const apr = rate === null ? null : rate * 24 * 365 * 100;
                const spreadBps = market.mark_price > 0 ? ((market.ask - market.bid) / market.mark_price) * 10_000 : 0;
                return (
                  <tr key={exchange.id}>
                    <td><strong className="capitalize">{exchange.name}</strong><div className="text-[10px] text-emerald-400 flex items-center gap-1 mt-0.5"><span className="status-dot" /> {exchange.status}</div></td>
                    <td><span className={`font-mono font-medium ${rate === null ? "text-slate-500" : rate >= 0 ? "text-emerald-300" : "text-rose-400"}`}>{rate === null ? "—" : `${rate >= 0 ? "+" : ""}${(rate * 100).toFixed(4)}%`}</span></td>
                    <td className="font-mono font-bold text-cyan">{apr === null ? "—" : `${apr >= 0 ? "+" : ""}${apr.toFixed(2)}%`}</td>
                    <td className="font-mono text-slate-200">${market.mark_price.toLocaleString(undefined, { minimumFractionDigits: 2 })}</td>
                    <td className="font-mono text-xs text-slate-400">${market.bid.toFixed(1)} / ${market.ask.toFixed(1)} <span className="ml-1 text-[10px] text-slate-500">({spreadBps.toFixed(1)} bps)</span></td>
                    <td className="font-mono text-slate-300">${(market.open_interest / 1_000_000).toFixed(2)}M</td>
                    <td><span className="tag receive">{market.funding_cycle_at ? new Date(market.funding_cycle_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}</span></td>
                    <td><button onClick={() => onSelectVenue?.(exchange.id)} className="button text-xs">Inspect Venue →</button></td>
                  </tr>
                );
              })}
              {!venueMarkets.length && <tr><td colSpan={8} className="empty-state">No venues currently track {normSym}.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel overflow-hidden">
        <div className="section-heading">
          <div><p className="eyebrow text-emerald-400">Historical Arbitrage Spreads</p><h2>Confirmed Funding Opportunities for {normSym}</h2></div>
          <span className="count-badge">{coinArbs.length} spread pairs</span>
        </div>
        <div className="overflow-x-auto">
          <table>
            <thead><tr><th>Strategy Legs</th><th>Net APR</th><th>Confirmed Funding / h</th><th>Price Basis</th><th>Round-Trip Fee</th><th>Capacity</th><th>Execution</th></tr></thead>
            <tbody>
              {coinArbs.map((arb) => (
                <tr key={arb.id}>
                  <td><div className="font-medium text-white">{normSym}</div><div className="subline"><span className="tag receive">Long {arb.long_venue}</span><span className="tag pay">Short {arb.short_venue}</span></div></td>
                  <td className="font-mono text-lg font-bold text-emerald-400">{arb.net_apr_pct >= 0 ? "+" : ""}{arb.net_apr_pct.toFixed(2)}%</td>
                  <td className="font-mono text-xs"><span className="text-emerald-300">{arb.long_funding_rate >= 0 ? "+" : ""}{(arb.long_funding_rate * 100).toFixed(4)}%</span><span className="text-slate-500"> → </span><span className="text-rose-300">{arb.short_funding_rate >= 0 ? "+" : ""}{(arb.short_funding_rate * 100).toFixed(4)}%</span><div className="text-[10px] text-slate-400">{arb.historical_snapshots_count} aligned cycles</div></td>
                  <td className={Math.abs(arb.basis_bps) > 30 ? "text-amber font-mono" : "text-slate-300 font-mono"}>{arb.basis_bps.toFixed(1)} bps</td>
                  <td className="font-mono text-slate-300">{arb.round_trip_fee_bps.toFixed(1)} bps</td>
                  <td className="font-mono text-slate-300">${(arb.capacity_usd / 1000).toFixed(0)}k</td>
                  <td><button onClick={() => onSelectSimulate?.(arb)} className="button text-xs">Simulate</button></td>
                </tr>
              ))}
              {!coinArbs.length && <tr><td colSpan={7} className="empty-state">No aligned confirmed funding spreads found for {normSym}.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
