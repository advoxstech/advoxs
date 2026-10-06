type StatTileTone = "neutral" | "good" | "warning" | "critical";

const TONE_CLASS: Record<StatTileTone, string> = {
  neutral: "text-ink",
  good: "text-accent",
  warning: "text-brass-ink",
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
    <div className="rounded-none border border-line bg-surface p-5">
      <p className="font-mono text-micro uppercase tracking-[0.12em] text-muted">{label}</p>
      <p
        className={`mt-2 break-words font-display text-2xl font-semibold sm:text-3xl ${TONE_CLASS[tone]}`}
      >
        {value}
      </p>
    </div>
  );
}
