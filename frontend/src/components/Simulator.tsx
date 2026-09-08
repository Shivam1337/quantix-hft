import type { Opportunity, Simulation, SimulationAccount } from "../types";

type Props = {
  items: Opportunity[];
  selected: Opportunity | null;
  capital: string;
  result: Simulation | null;
  account: SimulationAccount | null;
  onSelect: (id: string) => void;
  onRun: () => void;
  onReset: () => void;
};

export function Simulator({
  items,
  selected,
  capital,
  result,
  account,
  onSelect,
  onRun,
  onReset,
}: Props) {
  const initialBal = account?.initial_balance ?? 10000;
  const currentBal = account?.current_balance ?? 10000;
  const allocatedBal = account?.allocated_balance ?? 0;
  const realizedPnl = account?.total_realized_pnl ?? 0;
  const legSize = currentBal / 2;

  return (
    <div className="col-span-12 grid grid-cols-12 gap-5">
      {/* Account Balance and Reset Panel */}
      <section className="panel col-span-12 lg:col-span-5">
        <div className="section-heading">
          <div>
            <p className="eyebrow text-cyan">Simulation Account</p>
            <h2>Fixed Capital & Balance</h2>
          </div>
          <button
            onClick={onReset}
            className="button button-danger text-xs font-semibold py-1.5 px-3 shadow-lg shadow-rose-950/30"
          >
            Reset Trades & Balance
          </button>
        </div>

        <div className="p-5 space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="rounded-lg bg-slate-900/80 border border-slate-800 p-3">
              <span className="text-xs text-slate-400 block mb-1">Fixed Starting Balance</span>
              <strong className="text-xl font-mono text-slate-100">${initialBal.toLocaleString(undefined, { minimumFractionDigits: 2 })}</strong>
            </div>
            <div className="rounded-lg bg-slate-900/80 border border-slate-800 p-3">
              <span className="text-xs text-slate-400 block mb-1">Current Balance</span>
              <strong className={`text-xl font-mono ${currentBal >= initialBal ? "text-emerald-400" : "text-rose-400"}`}>
                ${currentBal.toLocaleString(undefined, { minimumFractionDigits: 2 })}
              </strong>
            </div>
          </div>

          <div className="rounded-lg bg-slate-900/60 border border-slate-800 p-3.5 space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">Position Allocation (50% per leg)</span>
              <span className="font-mono text-amber font-semibold">${legSize.toLocaleString(undefined, { minimumFractionDigits: 2 })} / leg</span>
            </div>
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400">Capital Deployed</span>
              <span className="font-mono text-slate-200">${allocatedBal.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
            </div>
            <div className="flex items-center justify-between text-xs border-t border-slate-800 pt-2">
              <span className="text-slate-400">Total Realized PnL</span>
              <span className={`font-mono font-bold ${realizedPnl >= 0 ? "text-emerald-400" : "text-rose-400"}`}>
                {realizedPnl >= 0 ? "+" : ""}${realizedPnl.toFixed(2)}
              </span>
            </div>
          </div>

          <div className="rounded-lg bg-cyan-950/20 border border-cyan-800/30 p-3 text-xs text-slate-300">
            <div className="flex items-center gap-2 mb-1">
              <span className="inline-block w-2 h-2 rounded-full bg-cyan animate-pulse" />
              <strong className="text-cyan">Autonomous Sizing & Decision Rules</strong>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Our system takes the account balance, divides it in half, and sizes each leg equally (50% Long, 50% Short).
              Positions are opened and closed automatically based on up to 3 days of historical funding snapshots stored in DB.
            </p>
          </div>
        </div>
      </section>

      {/* Carry Projection Tool */}
      <section className="panel col-span-12 lg:col-span-7">
        <div className="section-heading">
          <div>
            <p className="eyebrow text-amber">Hypothetical Carry Sizer</p>
            <h2>Spread Calculator</h2>
          </div>
          <span className="text-xs text-slate-500">Autonomous Paper Model</span>
        </div>

        <div className="grid gap-4 p-5 md:grid-cols-[1fr_auto]">
          <div>
            <label className="field-label">
              Opportunity Spread
              <select className="field" value={selected?.id ?? ""} onChange={(e) => onSelect(e.target.value)}>
                <option value="">Choose a spread</option>
                {items.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.symbol} · {item.long_venue} / {item.short_venue} · {item.net_apr_pct.toFixed(1)}% APR
                    {item.historical_3d_apr_pct !== undefined && item.historical_3d_apr_pct !== null
                      ? ` (3d: ${item.historical_3d_apr_pct.toFixed(1)}%)`
                      : ""}
                  </option>
                ))}
              </select>
            </label>
            <p className="mt-3 text-sm text-slate-400">
              Uses ${Number(capital || initialBal).toLocaleString()} capital over 30 days.
            </p>
          </div>
          <button className="button button-primary self-end" disabled={!selected} onClick={onRun}>
            Project Carry
          </button>
        </div>

        {result && (
          <div className="metric-grid border-t border-slate-800">
            <div>
              <span>Hourly cashflow</span>
              <strong>${result.projected_hourly_cashflow_usd.toFixed(2)}</strong>
            </div>
            <div>
              <span>30d net profit</span>
              <strong className="text-cyan">${result.projected_net_profit_usd.toFixed(2)}</strong>
            </div>
            <div>
              <span>Projected return</span>
              <strong>{result.projected_return_pct.toFixed(2)}%</strong>
            </div>
            <div>
              <span>Round-trip fees</span>
              <strong>${result.estimated_round_trip_fees_usd.toFixed(2)}</strong>
            </div>
            <div>
              <span>Fee breakeven</span>
              <strong>{result.fee_breakeven_hours ? `${result.fee_breakeven_hours.toFixed(1)}h` : "—"}</strong>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

