import type { Opportunity, Position } from "../types";

type Props = {
  positions: Position[];
  opportunities?: Opportunity[];
  onReset?: () => void;
};

export function PositionTable({ positions, opportunities = [], onReset }: Props) {
  return (
    <section className="panel col-span-12 lg:col-span-5">
      <div className="section-heading">
        <div>
          <p className="eyebrow text-cyan">Autonomous Portfolio</p>
          <h2>Active positions</h2>
        </div>
        <div className="flex items-center gap-2">
          {onReset && (
            <button className="button button-danger text-xs py-1 px-2.5" onClick={onReset}>
              Reset Trades & Balance
            </button>
          )}
          <span className="count-badge">{positions.length}</span>
        </div>
      </div>
      <div className="divide-y divide-slate-800">
        {positions.map((position) => {
          const legNotional = position.leg_size_usd ?? position.size_usd ?? 0;
          const leverage = position.leverage ?? 1;
          const legMargin = position.margin_per_leg_usd ?? legNotional / leverage;
          const opp = opportunities.find(
            (o) =>
              o.symbol === position.symbol &&
              o.long_venue.toLowerCase() === position.long_venue.toLowerCase() &&
              o.short_venue.toLowerCase() === position.short_venue.toLowerCase()
          );

          const curLong = opp?.long_mark_price ?? position.current_long_price ?? position.long_entry_price;
          const curShort = opp?.short_mark_price ?? position.current_short_price ?? position.short_entry_price;
          const curBasis = opp?.basis_bps ?? position.current_basis_bps ?? position.entry_basis_bps;

          const longMove = (curLong - position.long_entry_price) / position.long_entry_price;
          const shortMove = (position.short_entry_price - curShort) / position.short_entry_price;
          const liveBasisPnl = (longMove + shortMove) * legNotional;

          const fundingPnl = position.funding_pnl_usd ?? 0;
          const settledFunding = position.settled_funding_pnl_usd ?? 0;
          const accruedFunding = position.accrued_funding_pnl_usd ?? 0;
          const fees = position.fees_usd ?? 0;
          const netPnl = fundingPnl + liveBasisPnl - fees;

          return (
            <div className="position-row p-4" key={position.id}>
              <div className="flex items-center justify-between">
                <div>
                  <strong className="text-base font-bold">{position.symbol}</strong>
                  <span className="ml-2 text-xs font-mono text-emerald-400 bg-emerald-950/60 border border-emerald-800/40 rounded px-1.5 py-0.5">
                    Autonomous
                  </span>
                </div>
                <span className="text-xs text-slate-400 font-mono">
                  {position.negative_hours ?? 0}/2 flip hrs
                </span>
              </div>
              <p className="subline mt-1 text-xs text-slate-300">
                Long <span className="font-semibold text-slate-100">{position.long_venue}</span> · Short{" "}
                <span className="font-semibold text-slate-100">{position.short_venue}</span> ·{" "}
                <span className="text-amber font-mono">${legMargin.toLocaleString()}</span> margin/leg · <span className="text-cyan font-mono">${legNotional.toLocaleString()}</span> notional/leg ({leverage}×)
              </p>

              {/* Current Prices on Both Exchanges */}
              <div className="mt-3 grid grid-cols-2 gap-3 rounded bg-slate-900/80 border border-slate-800/80 p-2.5">
                <div>
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="capitalize font-semibold text-slate-300">Long {position.long_venue}</span>
                    <span className="text-[10px] text-emerald-400 font-mono font-semibold">BUY</span>
                  </div>
                  <div className="mt-1 flex items-baseline justify-between text-xs">
                    <span className="text-[11px] text-slate-400 font-mono">
                      Entry: ${position.long_entry_price < 1 ? position.long_entry_price.toFixed(4) : position.long_entry_price.toFixed(4)}
                    </span>
                    <span className="font-bold font-mono text-slate-100">
                      ${curLong < 1 ? curLong.toFixed(4) : curLong.toFixed(4)}
                      <small className={`ml-1 text-[10px] font-normal ${longMove >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {longMove >= 0 ? "+" : ""}{(longMove * 100).toFixed(2)}%
                      </small>
                    </span>
                  </div>
                </div>

                <div className="border-l border-slate-800 pl-3">
                  <div className="flex items-center justify-between text-[11px]">
                    <span className="capitalize font-semibold text-slate-300">Short {position.short_venue}</span>
                    <span className="text-[10px] text-rose-400 font-mono font-semibold">SELL</span>
                  </div>
                  <div className="mt-1 flex items-baseline justify-between text-xs">
                    <span className="text-[11px] text-slate-400 font-mono">
                      Entry: ${position.short_entry_price < 1 ? position.short_entry_price.toFixed(4) : position.short_entry_price.toFixed(4)}
                    </span>
                    <span className="font-bold font-mono text-slate-100">
                      ${curShort < 1 ? curShort.toFixed(4) : curShort.toFixed(4)}
                      <small className={`ml-1 text-[10px] font-normal ${shortMove >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                        {shortMove >= 0 ? "+" : ""}{(shortMove * 100).toFixed(2)}%
                      </small>
                    </span>
                  </div>
                </div>
              </div>

              {position.open_reason && (
                <div className="mt-2 rounded bg-slate-900/90 border border-slate-800 p-2 text-xs text-slate-300">
                  <span className="text-[11px] font-semibold text-cyan block mb-0.5">DB Decision Reasoning:</span>
                  <p className="text-[11px] text-slate-300 leading-relaxed font-sans">{position.open_reason}</p>
                </div>
              )}

              {position.close_reason && (
                <div className="mt-2 rounded bg-rose-950/40 border border-rose-800/50 p-2 text-xs text-rose-300">
                  <span className="text-[11px] font-semibold block mb-0.5">Exit Reasoning:</span>
                  <p className="text-[11px] text-rose-200 leading-relaxed font-sans">{position.close_reason}</p>
                </div>
              )}

              <div className="mt-2.5 flex flex-wrap items-center gap-2 text-[11px] border-t border-slate-800/40 pt-2 text-slate-400">
                <span>
                  Funding total:{" "}
                  <span className={fundingPnl >= 0 ? "text-emerald-300 font-mono font-medium" : "text-amber font-mono font-medium"}>
                    {fundingPnl >= 0 ? `+$${fundingPnl.toFixed(2)}` : `-$${Math.abs(fundingPnl).toFixed(2)}`}
                  </span>
                </span>
                <span className="text-slate-600">·</span>
                <span>Settled: <span className="text-slate-300 font-mono">${settledFunding.toFixed(2)}</span></span>
                <span className="text-slate-600">·</span>
                <span>Estimate: <span className="text-cyan-300 font-mono">${accruedFunding.toFixed(2)}</span></span>
                <span className="text-slate-600">·</span>
                <span>
                  <span className="capitalize">{position.long_venue}</span>:{" "}
                  <span className={(position.long_funding_pnl_usd ?? 0) >= 0 ? "text-emerald-400 font-mono" : "text-rose-400 font-mono"}>
                    {(position.long_funding_pnl_usd ?? 0) >= 0
                      ? `+$${(position.long_funding_pnl_usd ?? 0).toFixed(2)}`
                      : `-$${Math.abs(position.long_funding_pnl_usd ?? 0).toFixed(2)}`}
                  </span>
                </span>
                <span className="text-slate-600">·</span>
                <span>
                  <span className="capitalize">{position.short_venue}</span>:{" "}
                  <span className={(position.short_funding_pnl_usd ?? 0) >= 0 ? "text-emerald-400 font-mono" : "text-rose-400 font-mono"}>
                    {(position.short_funding_pnl_usd ?? 0) >= 0
                      ? `+$${(position.short_funding_pnl_usd ?? 0).toFixed(2)}`
                      : `-$${Math.abs(position.short_funding_pnl_usd ?? 0).toFixed(2)}`}
                  </span>
                </span>
              </div>

              <div className="mt-2 flex items-center justify-between border-t border-slate-800/50 pt-2">
                <div className="flex items-center gap-3">
                  <span className={netPnl >= 0 ? "text-emerald-300 font-mono font-bold text-base" : "text-rose-300 font-mono font-bold text-base"}>
                    Net PnL ${netPnl.toFixed(2)}{" "}
                    <small className="text-slate-500 font-normal text-xs">(fees ${fees.toFixed(2)})</small>
                  </span>
                  <span className="text-slate-400 text-xs font-mono">
                    Basis PnL:{" "}
                    <span className={liveBasisPnl >= 0 ? "text-emerald-400 font-medium" : "text-rose-400 font-medium"}>
                      {liveBasisPnl >= 0 ? `+$${liveBasisPnl.toFixed(2)}` : `-$${Math.abs(liveBasisPnl).toFixed(2)}`}
                    </span>
                  </span>
                </div>
                <div className="text-right text-[11px] font-mono text-slate-400">
                  <span>Basis: {curBasis.toFixed(1)} bps</span>
                  <span className="text-slate-600 block text-[10px]">Entry: {position.entry_basis_bps.toFixed(1)} bps</span>
                </div>
              </div>
            </div>
          );
        })}
        {!positions.length && (
          <div className="empty-state">
            No open positions. Autonomous engine is monitoring 3-day funding history to execute.
          </div>
        )}
      </div>
    </section>
  );
}
