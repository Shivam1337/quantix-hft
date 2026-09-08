import { Simulator } from "../components/Simulator";
import type { Opportunity, Simulation, SimulationAccount } from "../types";

type Props = {
  opportunities: Opportunity[];
  selected: Opportunity | null;
  capital: string;
  simulation: Simulation | null;
  account: SimulationAccount | null;
  onSelectOpportunity: (item: Opportunity | null) => void;
  onRunSimulation: () => void;
  onResetSimulation: () => void;
};

export function SimulatorPage(p: Props) {
  return (
    <div className="grid grid-cols-12 gap-5">
      <Simulator
        items={p.opportunities}
        selected={p.selected}
        capital={p.capital}
        result={p.simulation}
        account={p.account}
        onSelect={(id) => p.onSelectOpportunity(p.opportunities.find((x) => x.id === id) ?? null)}
        onRun={p.onRunSimulation}
        onReset={p.onResetSimulation}
      />
    </div>
  );
}
