import type { FundingPayment, Opportunity, Position } from "../types";

type Props = {
  payments: FundingPayment[];
  activePositions?: Position[];
  opportunities?: Opportunity[];
};

export function FundingPaymentTable({ payments, activePositions = [], opportunities = [] }: Props) {
  const openPositions = activePositions.filter((p) => p.status === "open");
  const totalEntries = payments.length + openPositions.length;

  return (
    <section className="panel col-span-12 overflow-hidden">
      <div className="section-heading">
        <div>
          <p className="eyebrow text-cyan">Funding Cashflow Audit</p>
          <h2>Funding Received on Both Legs</h2>
        </div>
        <span className="count-badge">{totalEntries}</span>
      </div>
      <div className="overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th>Time / Cycle</th>
              <th>Symbol</th>
              <th>Long Leg (Venue · Yield)</th>
              <th>Short Leg (Venue · Yield)</th>
              <th>Net Funding</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {openPositions.map((pos) => {
              const opp = opportunities.find(
                (o) =>
                  o.symbol === pos.symbol &&
                  o.long_venue.toLowerCase() === pos.long_venue.toLowerCase() &&
                  o.short_venue.toLowerCase() === pos.short_venue.toLowerCase()
              );
              const longAmt = pos.long_funding_pnl_usd ?? 0;
              const shortAmt = pos.short_funding_pnl_usd ?? 0;
              const netAmt = pos.funding_pnl_usd ?? (longAmt + shortAmt);

              return (
                <tr key={`active-${pos.id}`} className="bg-cyan-950/20 border-l-2 border-l-cyan">
                  <td className="text-cyan font-mono text-xs flex items-center gap-1.5 pt-3.5">
                    <span className="relative flex h-2 w-2">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cyan opacity-75"></span>
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-cyan"></span>
                    </span>
                    Current Cycle (Live)
                  </td>
                  <td className="font-bold text-white">{pos.symbol}</td>
                  <td>
                    <div className="flex flex-col">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold capitalize text-slate-200">
                          {pos.long_venue}
                        </span>
                        {opp && (
                          <span className="text-[10px] text-slate-400 font-mono">
                            {(opp.long_funding_rate * 100).toFixed(4)}%
                          </span>
                        )}
                      </div>
                      <span
                        className={`font-mono text-xs font-medium ${
                          longAmt >= 0 ? "text-emerald-400" : "text-rose-400"
                        }`}
                      >
                        {longAmt >= 0 ? `+$${longAmt.toFixed(4)}` : `-$${Math.abs(longAmt).toFixed(4)}`}
                      </span>
                    </div>
                  </td>
                  <td>
                    <div className="flex flex-col">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold capitalize text-slate-200">
                          {pos.short_venue}
                        </span>
                        {opp && (
                          <span className="text-[10px] text-slate-400 font-mono">
                            {(opp.short_funding_rate * 100).toFixed(4)}%
                          </span>
                        )}
                      </div>
                      <span
                        className={`font-mono text-xs font-medium ${
                          shortAmt >= 0 ? "text-emerald-400" : "text-rose-400"
                        }`}
                      >
                        {shortAmt >= 0 ? `+$${shortAmt.toFixed(4)}` : `-$${Math.abs(shortAmt).toFixed(4)}`}
                      </span>
                    </div>
                  </td>
                  <td>
                    <span
                      className={`font-mono font-bold text-sm ${
                        netAmt >= 0 ? "text-emerald-400" : "text-amber"
                      }`}
                    >
                      {netAmt >= 0 ? `+$${netAmt.toFixed(4)}` : `-$${Math.abs(netAmt).toFixed(4)}`}
                    </span>
                  </td>
                  <td>
                    <span className="tag receive text-[11px] font-semibold tracking-wide">
                      ACCRUING
                    </span>
                  </td>
                </tr>
              );
            })}

            {payments.map((p) => {
              const cycleTime = new Date(p.cycle_at);
              return (
                <tr key={p.id}>
                  <td className="text-slate-400 font-mono text-xs">
                    {cycleTime.toLocaleDateString(undefined, { month: "short", day: "numeric" })}{" "}
                    {cycleTime.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </td>
                  <td className="font-semibold text-slate-200">{p.symbol}</td>
                  <td>
                    <div className="flex flex-col">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold capitalize text-slate-300">
                          {p.long_venue}
                        </span>
                        <span className="text-[10px] text-slate-400 font-mono">
                          {(p.long_rate * 100).toFixed(4)}%
                        </span>
                      </div>
                      <span
                        className={`font-mono text-xs ${
                          p.long_payment_usd >= 0 ? "text-emerald-400" : "text-rose-400"
                        }`}
                      >
                        {p.long_payment_usd >= 0
                          ? `+$${p.long_payment_usd.toFixed(2)}`
                          : `-$${Math.abs(p.long_payment_usd).toFixed(2)}`}
                      </span>
                    </div>
                  </td>
                  <td>
                    <div className="flex flex-col">
                      <div className="flex items-center gap-1.5">
                        <span className="text-xs font-semibold capitalize text-slate-300">
                          {p.short_venue}
                        </span>
                        <span className="text-[10px] text-slate-400 font-mono">
                          {(p.short_rate * 100).toFixed(4)}%
                        </span>
                      </div>
                      <span
                        className={`font-mono text-xs ${
                          p.short_payment_usd >= 0 ? "text-emerald-400" : "text-rose-400"
                        }`}
                      >
                        {p.short_payment_usd >= 0
                          ? `+$${p.short_payment_usd.toFixed(2)}`
                          : `-$${Math.abs(p.short_payment_usd).toFixed(2)}`}
                      </span>
                    </div>
                  </td>
                  <td>
                    <span
                      className={`font-mono font-bold ${
                        p.net_payment_usd >= 0 ? "text-emerald-400" : "text-amber"
                      }`}
                    >
                      {p.net_payment_usd >= 0
                        ? `+$${p.net_payment_usd.toFixed(2)}`
                        : `-$${Math.abs(p.net_payment_usd).toFixed(2)}`}
                    </span>
                  </td>
                  <td>
                    <span className="tag text-[11px] text-slate-400 bg-slate-800/60 border border-slate-700">
                      SETTLED
                    </span>
                  </td>
                </tr>
              );
            })}

            {!totalEntries && (
              <tr>
                <td colSpan={6} className="empty-state">
                  No funding payments recorded yet. Funding settlements occur every hourly cycle.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
