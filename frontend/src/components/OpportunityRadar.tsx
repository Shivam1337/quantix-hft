import { useMemo } from "react";
import type { Opportunity } from "../types";

type Props = {
  items: Opportunity[];
  capital: string;
  minApr: string;
  pair: string;
  onCapitalChange: (value: string) => void;
  onMinAprChange: (value: string) => void;
  onPairChange: (value: string) => void;
  onSimulate: (item: Opportunity) => void;
};

const rate = (value: number) => `${value >= 0 ? "+" : ""}${(value * 100).toFixed(4)}%`;
const venue = (value: string) => value.slice(0, 1).toUpperCase() + value.slice(1);

export function OpportunityRadar(props: Props) {
  const sortedItems = useMemo(
    () => [...props.items].sort((a, b) => b.net_apr_pct - a.net_apr_pct || a.id.localeCompare(b.id)),
    [props.items]
  );

  return (
    <section className="panel col-span-12 overflow-hidden lg:col-span-8">
      <div className="section-heading">
        <div>
          <p className="eyebrow text-cyan">Confirmed-history opportunity radar</p>
          <h2>Funding spread matrix</h2>
        </div>
          <span className="status-dot">Liquidity streaming</span>
      </div>
      <div className="grid gap-3 border-b border-slate-800 p-4 md:grid-cols-3">
        <label className="field-label">Min APR %<input className="field" type="number" value={props.minApr} onChange={(e) => props.onMinAprChange(e.target.value)} /></label>
        <label className="field-label">Pair<select className="field" value={props.pair} onChange={(e) => props.onPairChange(e.target.value)}><option value="">All venues</option><option value="hyperliquid/lighter">Hyperliquid / Lighter</option><option value="aevo/lighter">Aevo / Lighter</option><option value="aevo/hyperliquid">Aevo / Hyperliquid</option></select></label>
        <label className="field-label">Simulator margin USD<input className="field" type="number" min="1" value={props.capital} onChange={(e) => props.onCapitalChange(e.target.value)} /></label>
      </div>
      <div className="overflow-x-auto">
        <table>
          <thead><tr><th>Market / legs</th><th>Confirmed Net APR</th><th>Aligned History</th><th>Confirmed Funding / h</th><th>Basis</th><th>Open + close fees</th><th>Capacity</th><th /></tr></thead>
          <tbody>
            {sortedItems.map((item) => <tr key={item.id}>
              <td><strong>{item.symbol}</strong><span className="subline"><span className="tag receive">L {venue(item.long_venue)}</span><span className="tag pay">S {venue(item.short_venue)}</span></span></td>
              <td className="text-cyan font-semibold">{item.net_apr_pct.toFixed(1)}%</td>
              <td>
                {item.historical_3d_apr_pct !== undefined && item.historical_3d_apr_pct !== null ? (
                  <div className="flex flex-col">
                    <span className={item.historical_3d_apr_pct >= 0 ? "text-emerald-400 font-semibold" : "text-rose-400 font-semibold"}>
                      {item.historical_3d_apr_pct.toFixed(1)}%
                    </span>
                    <span className="text-[10px] text-slate-500">
                      {item.historical_snapshots_count} snaps · {item.spread_stability_pct?.toFixed(0) ?? 100}% stab
                    </span>
                  </div>
                ) : (
                    <span className="text-xs text-slate-500">Awaiting confirmed history</span>
                )}
              </td>
              <td><span className="text-emerald-300">{rate(item.long_funding_rate)}</span><span className="text-slate-500"> → </span><span className="text-rose-300">{rate(item.short_funding_rate)}</span></td>
              <td className={Math.abs(item.basis_bps) > 50 ? "text-amber" : "text-slate-300"}>{item.basis_bps.toFixed(1)} bps</td>
              <td className="text-slate-300">{item.round_trip_fee_bps.toFixed(1)} bps</td>
              <td>${(item.capacity_usd / 1000).toFixed(0)}k</td>
              <td><button className="button" onClick={() => props.onSimulate(item)}>Size</button></td>
            </tr>)}
          </tbody>
        </table>
        {!sortedItems.length && <div className="empty-state">No opportunities match the current filters.</div>}
      </div>
    </section>
  );
}
