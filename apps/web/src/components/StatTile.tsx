type StatTileTone = "neutral" | "good" | "warning" | "critical";

const TONE_CLASS: Record<StatTileTone, string> = {
  neutral: "text-ink",
  good: "text-accent",
  warning: "text-brass",
  critical: "text-danger",
};

export function StatTile({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: StatTileTone;
}) {
  return (
    <div className="rounded-sm border border-line bg-surface p-5">
      <p className="font-mono text-[10px] uppercase tracking-[0.15em] text-muted">{label}</p>
      <p className={`mt-2 font-display text-3xl font-semibold ${TONE_CLASS[tone]}`}>{value}</p>
    </div>
  );
}
