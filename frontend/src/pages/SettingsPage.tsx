import { SettingsPanel } from "../components/SettingsPanel";
import { WalletPanel } from "../components/WalletPanel";
import type { Settings, ThemeMode } from "../types";

type Props = {
  settings: Settings | null;
  theme: ThemeMode;
  onThemeChange: (mode: ThemeMode) => void;
  onSaveSettings: (val: Partial<Settings>) => Promise<void>;
};

export function SettingsPage(p: Props) {
  return (
    <div className="grid grid-cols-12 gap-5">
      <SettingsPanel
        settings={p.settings}
        onSave={p.onSaveSettings}
        theme={p.theme}
        onThemeChange={p.onThemeChange}
      />
      <WalletPanel />
    </div>
  );
}
