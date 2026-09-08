import { useState } from "react";
import type { Settings, ThemeMode } from "../types";

type Props = {
  settings: Settings | null;
  onSave: (values: Partial<Settings>) => Promise<void>;
  theme?: ThemeMode;
  onThemeChange?: (theme: ThemeMode) => void;
};

export function SettingsPanel({ settings, onSave, theme = "system", onThemeChange }: Props) {
  const [apr, setApr] = useState("");
  const [basis, setBasis] = useState("");
  const [history, setHistory] = useState("");
  const [stability, setStability] = useState("");
  const [aprRatio, setAprRatio] = useState("");
  const [auto, setAuto] = useState<boolean | null>(null);

  if (!settings) {
    return (
      <section className="panel col-span-12 lg:col-span-6">
        <div className="section-heading"><h2>Configuration</h2></div>
        <div className="empty-state">Loading controls…</div>
      </section>
    );
  }

  const save = async () =>
    onSave({
      min_apr: apr ? Number(apr) : settings.min_apr,
      basis_threshold_bps: basis ? Number(basis) : settings.basis_threshold_bps,
      entry_min_history_snapshots: history ? Number(history) : settings.entry_min_history_snapshots,
      entry_min_spread_stability_pct: stability ? Number(stability) : settings.entry_min_spread_stability_pct,
      entry_max_apr_ratio: aprRatio ? Number(aprRatio) : settings.entry_max_apr_ratio,
      auto_unwind: auto ?? settings.auto_unwind,
    });

  const themeOptions: { id: ThemeMode; label: string; icon: string; desc: string }[] = [
    { id: "dark", label: "Dark", icon: "🌙", desc: "Cyberpunk trading theme" },
    { id: "light", label: "Light", icon: "☀️", desc: "Daylight contrast" },
    { id: "system", label: "System", icon: "💻", desc: "Auto-sync with OS" },
  ];

  return (
    <div className="col-span-12 space-y-6">
      {/* Theme Switching Section */}
      <section className="panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow text-cyan">Interface</p>
            <h2>Appearance & Theme Mode</h2>
          </div>
          <span className="count-badge uppercase">{theme} mode</span>
        </div>
        <div className="p-5">
          <p className="mb-4 text-xs text-slate-400">
            Choose your preferred display theme. System mode automatically detects your operating system's dark/light appearance.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {themeOptions.map((opt) => {
              const active = theme === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  onClick={() => onThemeChange?.(opt.id)}
                  className={`flex flex-col items-start rounded-xl border p-4 text-left transition ${
                    active
                      ? "border-cyan bg-cyan/10 shadow-[0_0_15px_rgba(34,211,238,0.15)]"
                      : "border-slate-800 bg-slate-900/40 hover:border-slate-700"
                  }`}
                >
                  <div className="flex w-full items-center justify-between">
                    <span className="text-xl">{opt.icon}</span>
                    {active && <span className="h-2 w-2 rounded-full bg-cyan" />}
                  </div>
                  <strong className={`mt-2 text-sm ${active ? "text-cyan" : "text-white"}`}>
                    {opt.label}
                  </strong>
                  <span className="text-[11px] text-slate-400">{opt.desc}</span>
                </button>
              );
            })}
          </div>
        </div>
      </section>

      {/* Risk Controls Section */}
      <section className="panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow text-amber">Guardrails</p>
            <h2>Risk & Strategy Parameters</h2>
          </div>
          <span className={settings.auto_unwind ? "status-dot" : "text-xs text-rose-300"}>
            {settings.auto_unwind ? "Auto-unwind on" : "Alerts only"}
          </span>
        </div>
        <div className="grid gap-4 p-5">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="field-label">
              APR floor %
              <input
                className="field"
                type="number"
                value={apr || settings.min_apr}
                onChange={(e) => setApr(e.target.value)}
              />
            </label>
            <label className="field-label">
              Basis threshold bps
              <input
                className="field"
                type="number"
                value={basis || settings.basis_threshold_bps}
                onChange={(e) => setBasis(e.target.value)}
              />
            </label>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <label className="field-label">
              Minimum history snapshots
              <input
                className="field"
                type="number"
                min="2"
                value={history || settings.entry_min_history_snapshots}
                onChange={(e) => setHistory(e.target.value)}
              />
            </label>
            <label className="field-label">
              Minimum spread stability %
              <input
                className="field"
                type="number"
                min="0"
                max="100"
                value={stability || settings.entry_min_spread_stability_pct}
                onChange={(e) => setStability(e.target.value)}
              />
            </label>
            <label className="field-label">
              Max current/history APR ratio
              <input
                className="field"
                type="number"
                min="1.01"
                step="0.1"
                value={aprRatio || settings.entry_max_apr_ratio}
                onChange={(e) => setAprRatio(e.target.value)}
              />
            </label>
          </div>
          <label className="flex items-center gap-3 py-2 text-sm text-slate-300">
            <input
              type="checkbox"
              checked={auto ?? settings.auto_unwind}
              onChange={(e) => setAuto(e.target.checked)}
            />
            Close a position after two negative funding hours or a basis breach.
          </label>
          <button className="button button-primary w-fit" onClick={save}>
            Save parameters
          </button>
        </div>
      </section>
    </div>
  );
}
