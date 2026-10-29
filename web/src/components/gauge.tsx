import { arc, arcLength, polar } from "@/lib/geometry";
import type { Estimate } from "@/lib/schemas";
import { signed } from "@/lib/format";

const THIN = 20;

/** A knob that reads one estimate: the band is its interval, the notch its value. */
export function Gauge({
  id,
  title,
  estimate,
  span,
  colors,
  size = 196,
  left = "quicker",
  right = "slower",
  caption,
  threshold = THIN,
  digits = 2,
}: {
  id: string;
  title: string;
  estimate: Estimate | null;
  span: number;
  colors: [string, string];
  size?: number;
  left?: string;
  right?: string;
  caption?: string;
  threshold?: number;
  digits?: number;
}) {
  const angle = (value: number) => Math.max(-135, Math.min(135, (value / span) * 135));
  const ticks = Array.from({ length: 28 }, (_, i) => {
    const a = -135 + i * 10;
    const big = i % 9 === 0 || i === 27;
    const [x1, y1] = polar(125, 125, 120, a);
    const [x2, y2] = polar(125, 125, big ? 110 : 115, a);
    return { x1, y1, x2, y2, big };
  });
  const thin = estimate !== null && estimate.samples < threshold;
  const [c0, c1] = thin ? ["#4e5058", "#6a6c75"] : colors;
  const from = estimate ? angle(estimate.low) : 0;
  const to = estimate ? angle(estimate.high) : 0;
  const length = arcLength(84, from, to);
  return (
    <figure className="flex flex-col items-center gap-2">
      <div className="pane relative shrink-0 rounded-full" style={{ width: size, height: size }}>
        <div className="pane-well absolute rounded-full" style={{ inset: size * 0.098 }} />
        <div className="pane absolute rounded-full" style={{ inset: size * 0.235 }} />
        <svg
          viewBox="0 0 250 250"
          role="img"
          aria-label={
            estimate
              ? `${title}: ${signed(estimate.value, digits)}, interval ${signed(estimate.low, digits)} to ${signed(estimate.high, digits)}, from ${estimate.samples} races`
              : `${title}: no estimate`
          }
          className="absolute inset-0 h-full w-full"
        >
          <defs>
            <linearGradient id={id} x1="0" y1="0" x2="1" y2="0">
              <stop offset="0" stopColor={c0} />
              <stop offset="1" stopColor={c1} />
            </linearGradient>
          </defs>
          {ticks.map((t, i) => (
            <line
              key={i}
              x1={t.x1}
              y1={t.y1}
              x2={t.x2}
              y2={t.y2}
              stroke={t.big ? "#5c5e66" : "#26272c"}
              strokeWidth={t.big ? 2 : 1.4}
              strokeLinecap="round"
            />
          ))}
          <path d={arc(125, 125, 84, -135, 135)} fill="none" stroke="#1d1e22" strokeWidth="10" strokeLinecap="round" />
          {estimate && (
            <>
              <path
                className="draw"
                d={arc(125, 125, 84, from, to)}
                fill="none"
                stroke={`url(#${id})`}
                strokeWidth="10"
                strokeLinecap="round"
                style={
                  {
                    "--len": length,
                    strokeDasharray: `${length} ${length}`,
                    filter: thin ? "none" : `drop-shadow(0 0 6px ${c1})`,
                  } as React.CSSProperties
                }
              />
              <g
                className="swing"
                style={
                  {
                    transform: `rotate(${angle(estimate.value)}deg)`,
                    transformOrigin: "125px 125px",
                    transformBox: "view-box",
                    "--from": "-135deg",
                  } as React.CSSProperties
                }
              >
                <line x1="125" y1="33" x2="125" y2="49" stroke="#ffffff" strokeWidth="4" strokeLinecap="round" />
              </g>
              <text x="125" y="132" textAnchor="middle" fill="#ececef" fontFamily="var(--font-mono)" fontSize="24">
                {signed(estimate.value, digits)}
              </text>
              <text x="125" y="155" textAnchor="middle" fill="#7f818a" fontFamily="var(--font-mono)" fontSize="12">
                {signed(estimate.low, digits)} to {signed(estimate.high, digits)}
              </text>
            </>
          )}
          {!estimate && (
            <text x="125" y="132" textAnchor="middle" fill="#7f818a" fontFamily="var(--font-sans)" fontSize="13">
              no fit yet
            </text>
          )}
          <text x="58" y="232" textAnchor="middle" fill="#7f818a" fontSize="10">
            {left}
          </text>
          <text x="192" y="232" textAnchor="middle" fill="#7f818a" fontSize="10">
            {right}
          </text>
        </svg>
      </div>
      <figcaption className="flex flex-col items-center gap-0.5 text-center">
        <span className="display text-sm">{title}</span>
        <span className={`text-[11px] ${thin ? "text-ink" : "text-mute"}`}>
          {caption ?? (estimate ? `${estimate.samples} races${thin ? ", too few to trust" : ""}` : "no comparable races")}
        </span>
      </figcaption>
    </figure>
  );
}
