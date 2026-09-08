import { ExchangeView } from "../components/ExchangeView";

type Props = {
  onSelectCoin: (symbol: string, venue: string) => void;
};

export function ExchangesPage({ onSelectCoin }: Props) {
  return <ExchangeView onSelectCoin={onSelectCoin} />;
}
