import type { ContactTag } from "@/lib/types";

export function ContactBadges({
  tags = [],
  humanOnly = false,
}: {
  tags?: ContactTag[];
  humanOnly?: boolean;
}) {
  return (
    <span className="flex flex-wrap gap-1 text-xs">
      {tags.map((tag) => (
        <span
          key={tag.id}
          className="rounded border border-line bg-surface px-2 py-0.5 text-ink"
        >
          <span
            aria-hidden
            className="mr-1 inline-block h-2 w-2 rounded-full"
            style={{ background: tag.color }}
          />
          {tag.name}
        </span>
      ))}
      {humanOnly && (
        <span className="rounded bg-brass-soft px-2 py-0.5 text-ink">
          Exclusivamente humano
        </span>
      )}
    </span>
  );
}
