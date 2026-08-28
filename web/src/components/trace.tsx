import { label } from "@/lib/format";
import { window as traceWindow } from "@/lib/window";

export type TracePoint = {
  key: string;
  date: string;
  value: number;
  wet: boolean;
  circuit: string;
};

const WIDTH = 720;
const HEIGHT = 220;
const PAD = { top: 14, right: 8, bottom: 26, left: 40 };

export function Trace({
  points,
  caption,
  fastEnd,
  slowEnd,
}: {
  points: TracePoint[];
  caption: string;
  fastEnd: string;
  slowEnd: string;
}) {
  if (points.length === 0) {
    return (
      <div className="cut-face flex h-24 items-center px-4">
        <span className="engraved text-lume-dim">{caption}</span>
      </div>
    );
  }

  const times = points.map((point) => new Date(point.date).getTime());
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const frame = traceWindow(points.map((point) => point.value));
  const beyond = points.filter(
    (point) => point.value < frame.low || point.value > frame.high,
  ).length;

  const x = (time: number) =>
    PAD.left + ((time - minTime) / (maxTime - minTime || 1)) * (WIDTH - PAD.left - PAD.right);
  // smaller is faster, so the fast end is the top of the plate
  const y = (value: number) => {
    const clamped = Math.min(Math.max(value, frame.low), frame.high);
    const share = (clamped - frame.low) / (frame.high - frame.low || 1);
    return PAD.top + share * (HEIGHT - PAD.top - PAD.bottom);
  };

  const years = Array.from(new Set(points.map((point) => point.date.slice(0, 4))));

  return (
    <figure>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} className="h-56 w-full" role="img" aria-label={caption}>
        {frame.ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(tick)}
              y2={y(tick)}
              stroke={tick === 0 ? "var(--color-engrave-lit)" : "var(--color-engrave)"}
              strokeWidth={1}
            />
            <text
              x={PAD.left - 6}
              y={y(tick) + 3}
              textAnchor="end"
              className="figure"
              fontSize={9}
              fill="var(--color-steel)"
            >
              {tick.toFixed(1)}
            </text>
          </g>
        ))}
        {years.map((year) => {
          const first = points.find((point) => point.date.startsWith(year));
          if (!first) return null;
          const at = x(new Date(first.date).getTime());
          return (
            <g key={year}>
              <line
                x1={at}
                x2={at}
                y1={PAD.top}
                y2={HEIGHT - PAD.bottom}
                stroke="var(--color-engrave)"
                strokeWidth={1}
              />
              <text
                x={Math.min(at + 4, WIDTH - PAD.right - 22)}
                y={HEIGHT - PAD.bottom + 14}
                className="figure"
                fontSize={9}
                fill="var(--color-steel)"
              >
                {year}
              </text>
            </g>
          );
        })}
        {points.map((point) => {
          const cx = x(new Date(point.date).getTime());
          const cy = y(point.value);
          const outside = point.value < frame.low || point.value > frame.high;
          const title = `${point.date} ${label(point.circuit)} ${point.value.toFixed(2)}${
            point.wet ? " (wet)" : ""
          }${outside ? " — beyond the scale" : ""}`;
          if (outside) {
            const up = point.value < frame.low;
            return (
              <path
                key={point.key}
                d={`M${cx - 4} ${cy + (up ? 5 : -5)} L${cx} ${cy} L${cx + 4} ${cy + (up ? 5 : -5)}`}
                fill="none"
                stroke={point.wet ? "var(--color-wet)" : "var(--color-lume)"}
                strokeWidth={1.5}
              >
                <title>{title}</title>
              </path>
            );
          }
          return point.wet ? (
            <circle
              key={point.key}
              cx={cx}
              cy={cy}
              r={3.5}
              fill="none"
              stroke="var(--color-wet)"
              strokeWidth={1.5}
            >
              <title>{title}</title>
            </circle>
          ) : (
            <line
              key={point.key}
              x1={cx}
              x2={cx}
              y1={cy - 4}
              y2={cy + 4}
              stroke="var(--color-lume)"
              strokeWidth={1.5}
            >
              <title>{title}</title>
            </line>
          );
        })}
        <text
          x={WIDTH - PAD.right}
          y={9}
          textAnchor="end"
          className="figure"
          fontSize={9}
          fill="var(--color-steel)"
        >
          {fastEnd}
        </text>
        <text
          x={WIDTH - PAD.right}
          y={HEIGHT - PAD.bottom - 4}
          textAnchor="end"
          className="figure"
          fontSize={9}
          fill="var(--color-steel)"
        >
          {slowEnd}
        </text>
      </svg>
      <figcaption className="engraved mt-2 normal-case tracking-normal text-lume-dim">
        {caption}
        {beyond > 0 &&
          (beyond === 1
            ? " One race sits beyond the scale and is drawn as a caret on the edge."
            : ` ${beyond} races sit beyond the scale and are drawn as carets on the edge.`)}
      </figcaption>
    </figure>
  );
}
