import { PositionTable } from "../components/PositionTable";
import { TradeLogTable } from "../components/TradeLogTable";
import type { Position, SimulationAccount, TradeLog } from "../types";

type Props = {
  positions: Position[];
  logs: TradeLog[];
  account?: SimulationAccount | null;
  onResetSimulation?: () => void;
};

export function PositionsPage({ positions, logs, account, onResetSimulation }: Props) {
  const currentBal = account?.current_balance ?? 10000;
  const initialBal = account?.initial_balance ?? 10000;
  const allocated = account?.allocated_balance ?? 0;
  const legSize = currentBal / 2;

  return (
    <div className="grid grid-cols-12 gap-5">
      {account && (
        <div className="col-span-12 rounded-lg bg-slate-900/60 border border-slate-800 p-4 flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-6">
            <div>
              <span className="text-xs text-slate-400 block">Account Balance</span>
              <span className="text-lg font-mono font-bold text-slate-100">
                ${currentBal.toLocaleString(undefined, { minimumFractionDigits: 2 })}
              </span>
            </div>
            <div>
              <span className="text-xs text-slate-400 block">Per-Leg Sizing (50%)</span>
              <span className="text-lg font-mono font-bold text-amber">
                ${legSize.toLocaleString(undefined, { minimumFractionDigits: 2 })}
              </span>
            </div>
            <div>
              <span className="text-xs text-slate-400 block">Allocated / Active</span>
              <span className="text-lg font-mono font-semibold text-cyan">
                ${allocated.toLocaleString(undefined, { minimumFractionDigits: 2 })}
              </span>
            </div>
          </div>
          {onResetSimulation && (
            <button
              onClick={onResetSimulation}
              className="button button-danger text-xs font-semibold py-1.5 px-3"
            >
              Reset All Trades & Balance
            </button>
          )}
        </div>
      )}
      <div className="col-span-12">
        <PositionTable positions={positions} onReset={onResetSimulation} />
      </div>
      <TradeLogTable logs={logs} />
    </div>
  );
}

