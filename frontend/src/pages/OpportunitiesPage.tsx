import { OpportunityRadar } from "../components/OpportunityRadar";
import type { Opportunity } from "../types";

type Props = {
  opportunities: Opportunity[];
  capital: string;
  minApr: string;
  pair: string;
  onCapitalChange: (val: string) => void;
  onMinAprChange: (val: string) => void;
  onPairChange: (val: string) => void;
  onSelectSimulate: (item: Opportunity) => void;
};

export function OpportunitiesPage(p: Props) {
  return (
    <div className="grid grid-cols-12 gap-5">
      <OpportunityRadar
        items={p.opportunities}
        capital={p.capital}
        minApr={p.minApr}
        pair={p.pair}
        onCapitalChange={p.onCapitalChange}
        onMinAprChange={p.onMinAprChange}
        onPairChange={p.onPairChange}
        onSimulate={p.onSelectSimulate}
      />
    </div>
  );
}
