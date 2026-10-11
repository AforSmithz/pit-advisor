import { signed } from "@/lib/format";
import { circuitName } from "@/lib/teams";
import type { TeammateSample } from "@/lib/schemas";

export function TeammateBars({ samples, code }: { samples: TeammateSample[]; code: string }) {
  if (samples.length === 0) {
    return <p className="text-sm text-ink-3">No shared races with a teammate yet.</p>;
  }
  const reach = Math.max(...samples.map((sample) => Math.abs(sample.delta)), 0.1);
  const slot = 348 / samples.length;
  return (
    <div className="inset flex-1 rounded-2xl p-1">
      <svg
        viewBox="0 0 360 120"
        role="img"
        aria-label={`${code} against teammates over ${samples.length} shared races, bars up where ${code} was quicker`}
        className="block h-full min-h-[110px] w-full"
      >
        <defs>
          <linearGradient id="mate-up" x1="0" y1="1" x2="0" y2="0">
            <stop offset="0" stopColor="#ff9a3c" />
            <stop offset="1" stopColor="#ff5f6d" />
          </linearGradient>
          <linearGradient id="mate-down" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#5a2bb8" />
            <stop offset="1" stopColor="#8a5cff" />
          </linearGradient>
        </defs>
        <line x1="6" x2="354" y1="60" y2="60" stroke="#2e2f35" />
        {samples.map((sample, i) => {
          const ahead = sample.delta < 0;
          const height = (Math.abs(sample.delta) / reach) * 50;
          const x = 6 + i * slot + slot * 0.15;
          const w = slot * 0.7;
          return (
            <g key={`${sample.season}-${sample.round}-${sample.teammate}`}>
              <rect x={x} y="8" width={w} height="104" rx="3" fill="#141417" />
              <rect
                x={x}
                y={ahead ? 60 - height : 60}
                width={w}
                height={Math.max(height, 1)}
                rx="3"
                fill={ahead ? "url(#mate-up)" : "url(#mate-down)"}
              >
                <title>
                  {`${circuitName(sample.circuit_id)} ${sample.season}, against ${sample.teammate}: ${signed(sample.delta)}%`}
                </title>
              </rect>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
