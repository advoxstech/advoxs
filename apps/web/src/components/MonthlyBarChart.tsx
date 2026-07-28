"use client";

import { useState } from "react";

import { formatBRL } from "@/lib/format";

type DataPoint = { month: string; total_brl: number };

const WIDTH = 600;
const HEIGHT = 160;
const PADDING = 24;

function monthLabel(month: string): string {
  const [year, monthNumber] = month.split("-");
  const date = new Date(Number(year), Number(monthNumber) - 1, 1);
  return date.toLocaleDateString("pt-BR", { month: "short", year: "2-digit" });
}

export function MonthlyBarChart({ data }: { data: DataPoint[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  if (data.length === 0) {
    return <p className="text-sm text-muted">Nenhum valor no período selecionado.</p>;
  }

  const maxTotal = Math.max(...data.map((d) => d.total_brl), 1);
  const innerWidth = WIDTH - PADDING * 2;
  const barWidth = innerWidth / data.length;
  const barGap = barWidth * 0.2;

  const bars = data.map((d, i) => {
    const barHeight = (d.total_brl / maxTotal) * (HEIGHT - PADDING * 2);
    return {
      x: PADDING + i * barWidth + barGap / 2,
      y: HEIGHT - PADDING - barHeight,
      width: barWidth - barGap,
      height: barHeight,
      ...d,
    };
  });

  const hovered = hoverIndex !== null ? bars[hoverIndex] : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        onMouseLeave={() => setHoverIndex(null)}
      >
        {bars.map((bar, i) => (
          <g key={bar.month}>
            <rect
              data-bar
              x={bar.x}
              y={bar.y}
              width={bar.width}
              height={Math.max(bar.height, 1)}
              fill={hoverIndex === i ? "var(--accent)" : "var(--accent-soft)"}
              onMouseEnter={() => setHoverIndex(i)}
            />
            <text
              x={bar.x + bar.width / 2}
              y={HEIGHT - PADDING + 14}
              textAnchor="middle"
              className="fill-muted text-[10px]"
            >
              {monthLabel(bar.month)}
            </text>
          </g>
        ))}
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-sm border border-line bg-ground px-2 py-1 text-xs text-ink shadow-sm"
          style={{
            left: `${((hovered.x + hovered.width / 2) / WIDTH) * 100}%`,
            top: `${(hovered.y / HEIGHT) * 100}%`,
          }}
        >
          {monthLabel(hovered.month)}: R$ {formatBRL(hovered.total_brl)}
        </div>
      )}
    </div>
  );
}
