import type { CurvePoint, ScoredModel } from "@/lib/schemas";

const WIDTH = 260;
const HEIGHT = 260;
const PAD = { top: 10, right: 10, bottom: 26, left: 30 };

const INK: Record<string, string> = {
  simulation: "var(--color-split)",
  grid: "var(--color-lume)",
  standings: "var(--color-wet)",
  last_race: "var(--color-lume-dim)",
};

function x(share: number): number {
  return PAD.left + share * (WIDTH - PAD.left - PAD.right);
}

function y(share: number): number {
  return HEIGHT - PAD.bottom - share * (HEIGHT - PAD.top - PAD.bottom);
}

function path(points: CurvePoint[]): string {
  return points
    .map(
      (point, index) =>
        `${index ? "L" : "M"}${x(point.forecast)} ${y(point.observed)}`,
    )
    .join(" ");
}

export function Reliability({
  event,
  scored,
  highlight,
}: {
  event: string;
  scored: ScoredModel[];
  highlight: string;
}) {
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  return (
    <figure>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="w-full"
        role="img"
        aria-label={`Reliability of the ${event} probability: what was forecast against what happened`}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(tick)}
              y2={y(tick)}
              stroke="var(--color-engrave)"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 5}
              y={y(tick) + 3}
              textAnchor="end"
              className="figure"
              fontSize={8}
              fill="var(--color-steel)"
            >
              {tick.toFixed(1)}
            </text>
            <text
              x={x(tick)}
              y={HEIGHT - PAD.bottom + 12}
              textAnchor="middle"
              className="figure"
              fontSize={8}
              fill="var(--color-steel)"
            >
              {tick.toFixed(1)}
            </text>
          </g>
        ))}
        {/* the diagonal is what a probability that means what it says looks like */}
        <line
          x1={x(0)}
          x2={x(1)}
          y1={y(0)}
          y2={y(1)}
          stroke="var(--color-engrave-lit)"
          strokeWidth={1}
          strokeDasharray="3 3"
        />
        {scored.map((model) => {
          const points = model.curves[event] ?? [];
          if (points.length < 2) return null;
          const lead = model.name === highlight;
          return (
            <g key={model.name}>
              <path
                d={path(points)}
                fill="none"
                stroke={INK[model.name] ?? "var(--color-steel)"}
                strokeWidth={lead ? 1.6 : 1}
                opacity={lead ? 1 : 0.7}
              />
              {lead &&
                points.map((point) => (
                  <circle
                    key={point.low}
                    cx={x(point.forecast)}
                    cy={y(point.observed)}
                    r={2.2}
                    fill="var(--color-split)"
                  >
                    <title>
                      {`forecast ${(point.forecast * 100).toFixed(0)}%, happened ${(
                        point.observed * 100
                      ).toFixed(0)}% over ${point.count} driver-races`}
                    </title>
                  </circle>
                ))}
            </g>
          );
        })}
        <text
          x={WIDTH - PAD.right}
          y={PAD.top + 8}
          textAnchor="end"
          className="engraved"
          fontSize={9}
          fill="var(--color-lume)"
        >
          {event}
        </text>
      </svg>
      <figcaption className="engraved mt-1 normal-case tracking-normal text-lume-dim">
        forecast across, observed up
      </figcaption>
    </figure>
  );
}

export function Series({
  rows,
  models,
}: {
  rows: {
    race_date: string;
    circuit_id: string;
    log_loss: Record<string, number>;
  }[];
  models: string[];
}) {
  const width = 720;
  const height = 150;
  const pad = { top: 10, right: 8, bottom: 22, left: 34 };
  const values = rows.flatMap((row) =>
    models.map((name) => row.log_loss[name] ?? 0),
  );
  const low = Math.min(...values);
  const high = Math.max(...values);
  const at = (index: number) =>
    pad.left +
    (index / Math.max(rows.length - 1, 1)) * (width - pad.left - pad.right);
  const up = (value: number) =>
    height -
    pad.bottom -
    ((value - low) / (high - low || 1)) * (height - pad.top - pad.bottom);

  return (
    <figure>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-36 w-full"
        role="img"
        aria-label="Log loss race by race across the holdout"
      >
        {[low, (low + high) / 2, high].map((tick) => (
          <g key={tick}>
            <line
              x1={pad.left}
              x2={width - pad.right}
              y1={up(tick)}
              y2={up(tick)}
              stroke="var(--color-engrave)"
            />
            <text
              x={pad.left - 5}
              y={up(tick) + 3}
              textAnchor="end"
              className="figure"
              fontSize={8}
              fill="var(--color-steel)"
            >
              {tick.toFixed(1)}
            </text>
          </g>
        ))}
        {models.map((name) => (
          <path
            key={name}
            d={rows
              .map(
                (row, index) =>
                  `${index ? "L" : "M"}${at(index)} ${up(row.log_loss[name] ?? low)}`,
              )
              .join(" ")}
            fill="none"
            stroke={INK[name] ?? "var(--color-steel)"}
            strokeWidth={name === "simulation" ? 1.6 : 1}
            opacity={name === "simulation" ? 1 : 0.55}
          />
        ))}
        {rows.map((row, index) => (
          <line
            key={row.race_date}
            x1={at(index)}
            x2={at(index)}
            y1={height - pad.bottom}
            y2={height - pad.bottom + 3}
            stroke="var(--color-engrave-lit)"
          >
            <title>{`${row.race_date} ${row.circuit_id}`}</title>
          </line>
        ))}
        <text
          x={pad.left}
          y={height - 4}
          className="figure"
          fontSize={8}
          fill="var(--color-steel)"
        >
          {rows[0]?.race_date.slice(0, 7)}
        </text>
        <text
          x={width - pad.right}
          y={height - 4}
          textAnchor="end"
          className="figure"
          fontSize={8}
          fill="var(--color-steel)"
        >
          {rows[rows.length - 1]?.race_date.slice(0, 7)}
        </text>
      </svg>
    </figure>
  );
}

export function Key({ models }: { models: string[] }) {
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-1">
      {models.map((name) => (
        <li key={name} className="flex items-center gap-2">
          <span
            className="h-px w-5"
            style={{ backgroundColor: INK[name] ?? "var(--color-steel)" }}
            aria-hidden
          />
          <span className="engraved">{name.replace(/_/g, " ")}</span>
        </li>
      ))}
    </ul>
  );
}
