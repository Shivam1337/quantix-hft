type Props = { label: string; value: string; detail?: string; accent?: "cyan" | "amber" | "white" };

export function MetricCard({ label, value, detail, accent = "cyan" }: Props) {
  const color = accent === "amber" ? "text-amber" : accent === "white" ? "text-white" : "text-cyan";
  return (
    <div className="panel p-4 shadow-glow">
      <p className="eyebrow">{label}</p>
      <p className={`mt-2 text-2xl font-semibold tracking-tight ${color}`}>{value}</p>
      {detail && <p className="mt-1 text-xs text-slate-400">{detail}</p>}
    </div>
  );
}
