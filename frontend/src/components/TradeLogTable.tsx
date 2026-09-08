import type { TradeLog } from "../types";

type Props = { logs: TradeLog[] };

export function TradeLogTable({ logs }: Props) {
  return (
    <section className="panel col-span-12 overflow-hidden">
      <div className="section-heading">
        <div>
          <p className="eyebrow text-cyan">Audit trail</p>
          <h2>Execution logs</h2>
        </div>
        <span className="count-badge">{logs.length}</span>
      </div>
      <div className="overflow-x-auto">
        <table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Venue</th>
              <th>Side</th>
              <th>Phase</th>
              <th>Size USD</th>
              <th>Filled Price</th>
              <th>Fee USD</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id}>
                <td className="text-slate-400">{new Date(log.created_at).toLocaleTimeString()}</td>
                <td className="font-semibold capitalize text-slate-200">{log.venue}</td>
                <td>
                  <span className={`tag ${log.side === "buy" ? "receive" : "pay"}`}>
                    {log.side.toUpperCase()}
                  </span>
                </td>
                <td className="capitalize text-slate-400">{log.phase}</td>
                <td className="font-mono text-slate-200">${log.size_usd.toLocaleString()}</td>
                <td className="font-mono text-cyan">
                  {log.price ? `$${log.price < 1 ? log.price.toFixed(4) : log.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}` : "-"}
                </td>
                <td className="font-mono text-slate-400">${log.fee_usd.toFixed(2)}</td>
                <td>
                  <span className={`tag ${log.status === "filled" ? "receive" : "pay"}`}>
                    {log.status}
                  </span>
                </td>
              </tr>
            ))}
            {!logs.length && (
              <tr>
                <td colSpan={8} className="empty-state">
                  No trade execution logs recorded yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
