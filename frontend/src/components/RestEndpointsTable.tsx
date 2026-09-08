import type { RestEndpointStat } from "../types";

type Props = {
  endpoints?: Record<string, RestEndpointStat[]>;
  venues: string[];
};

export function RestEndpointsTable({ endpoints, venues }: Props) {
  const hasEndpoints = venues.some((v) => (endpoints?.[v]?.length ?? 0) > 0);

  return (
    <div className="panel overflow-hidden">
      <div className="section-heading">
        <div>
          <p className="eyebrow text-cyan">REST Telemetry</p>
          <h2>Active REST Endpoints & Request Telemetry</h2>
        </div>
        <span className="count-badge">Per-Venue Ingestion & Polling</span>
      </div>

      {!hasEndpoints ? (
        <div className="p-8 text-center text-slate-400">
          Listening for outgoing REST calls across venues...
        </div>
      ) : (
        <div className="space-y-6 p-5">
          {venues.map((venue) => {
            const list = endpoints?.[venue] ?? [];
            return (
              <div key={venue} className="rounded-xl border border-slate-800/80 bg-slate-900/40 p-4">
                <div className="mb-3 flex items-center justify-between">
                  <span className="font-semibold uppercase tracking-wider text-white">
                    {venue}
                  </span>
                  <span className="text-xs text-slate-400">
                    {list.length} {list.length === 1 ? "endpoint" : "endpoints"} monitored
                  </span>
                </div>

                {list.length === 0 ? (
                  <p className="text-xs text-slate-500">No REST calls recorded for this venue yet.</p>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left">
                      <thead>
                        <tr className="border-b border-slate-800 text-xs text-slate-400">
                          <th className="py-2 font-medium">Method</th>
                          <th className="py-2 font-medium">Endpoint Pattern</th>
                          <th className="py-2 font-medium">Total Requests</th>
                          <th className="py-2 font-medium">Last Status</th>
                          <th className="py-2 font-medium">Last Invoked</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-800/60 text-xs">
                        {list.map((ep) => {
                          const isGet = ep.method === "GET";
                          const isOk = ep.last_status >= 200 && ep.last_status < 300;
                          return (
                            <tr key={`${ep.method}-${ep.endpoint}`} className="hover:bg-slate-800/30">
                              <td className="py-2.5">
                                <span
                                  className={`rounded px-1.5 py-0.5 font-mono text-[10px] font-bold ${
                                    isGet
                                      ? "bg-cyan/15 text-cyan border border-cyan/30"
                                      : "bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"
                                  }`}
                                >
                                  {ep.method}
                                </span>
                              </td>
                              <td className="py-2.5 font-mono text-slate-200">{ep.endpoint}</td>
                              <td className="py-2.5 font-mono font-semibold text-white">
                                {ep.calls_total.toLocaleString()}
                              </td>
                              <td className="py-2.5 font-mono">
                                <span
                                  className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                                    isOk
                                      ? "bg-emerald-500/10 text-emerald-300"
                                      : "bg-amber-500/10 text-amber-300"
                                  }`}
                                >
                                  {ep.last_status} {isOk ? "OK" : "ERR"}
                                </span>
                              </td>
                              <td className="py-2.5 font-mono text-slate-400">
                                {ep.last_called_at
                                  ? new Date(ep.last_called_at).toLocaleTimeString()
                                  : "Never"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
