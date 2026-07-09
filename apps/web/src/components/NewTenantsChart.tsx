"use client";

import { useState } from "react";

type DataPoint = { day: string; count: number };

const WIDTH = 600;
const HEIGHT = 160;
const PADDING = 24;

export function NewTenantsChart({ data }: { data: DataPoint[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  if (data.length === 0) {
    return <p className="text-sm text-muted">Sem novos escritórios nos últimos 30 dias.</p>;
  }

  const maxCount = Math.max(...data.map((d) => d.count), 1);
  const stepX = (WIDTH - PADDING * 2) / Math.max(data.length - 1, 1);

  const points = data.map((d, i) => ({
    x: PADDING + i * stepX,
    y: HEIGHT - PADDING - (d.count / maxCount) * (HEIGHT - PADDING * 2),
    day: d.day,
    count: d.count,
  }));

  const linePath = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
  const firstPoint = points[0]!;
  const lastPoint = points[points.length - 1]!;
  const areaPath = `${linePath} L${lastPoint.x},${HEIGHT - PADDING} L${firstPoint.x},${HEIGHT - PADDING} Z`;

  const hovered = hoverIndex !== null ? points[hoverIndex] : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        onMouseLeave={() => setHoverIndex(null)}
      >
        <path d={areaPath} fill="var(--accent-soft)" />
        <path
          d={linePath}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={2}
          strokeLinecap="round"
        />
        {points.map((p, i) => (
          <rect
            key={p.day}
            x={p.x - stepX / 2}
            y={0}
            width={stepX}
            height={HEIGHT}
            fill="transparent"
            onMouseEnter={() => setHoverIndex(i)}
          />
        ))}
        {hovered && (
          <circle
            cx={hovered.x}
            cy={hovered.y}
            r={4}
            fill="var(--accent)"
            stroke="var(--surface)"
            strokeWidth={2}
          />
        )}
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-sm border border-line bg-ground px-2 py-1 text-xs text-ink shadow-sm"
          style={{ left: `${(hovered.x / WIDTH) * 100}%`, top: `${(hovered.y / HEIGHT) * 100}%` }}
        >
          {hovered.day}: {hovered.count}
        </div>
      )}
    </div>
  );
}
