import type { Position } from "../types";

type Props = { positions: Position[]; onReset?: () => void };

export function PositionTable({ positions, onReset }: Props) {
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
          const legSize = position.leg_size_usd ?? position.size_usd ?? 0;
          const fundingPnl = position.funding_pnl_usd ?? 0;
          const basisPnl = position.basis_pnl_usd ?? 0;
          const fees = position.fees_usd ?? 0;
          const netPnl = fundingPnl + basisPnl - fees;
          const basisBps = position.entry_basis_bps ?? 0;
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
                <span className="text-amber font-mono">${(legSize || 0).toLocaleString()}</span> / leg (50% of balance)
              </p>

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

              <div className="mt-3 flex items-center justify-between border-t border-slate-800/50 pt-2">
                <span className={netPnl >= 0 ? "text-emerald-300 font-mono font-medium" : "text-rose-300 font-mono font-medium"}>
                  Net PnL ${netPnl.toFixed(2)}{" "}
                  <small className="text-slate-500 font-normal">(fees ${fees.toFixed(2)})</small>
                </span>
                <span className="text-[11px] text-slate-500 font-mono">
                  Basis: {basisBps.toFixed(1)} bps
                </span>
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

