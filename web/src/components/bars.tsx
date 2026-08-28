import { label } from "@/lib/format";

export function Share({
  rows,
  total,
}: {
  rows: { name: string; count: number }[];
  total: number;
}) {
  return (
    <ul>
      {rows.map((row) => (
        <li key={row.name} className="border-t border-engrave py-1.5">
          <div className="flex items-baseline justify-between gap-3">
            <span className="engraved normal-case tracking-normal text-lume-dim">{row.name}</span>
            <span className="figure text-xs text-lume">{row.count.toLocaleString("en-GB")}</span>
          </div>
          <div className="mt-1 h-1 bg-engrave">
            <div
              className="h-1 bg-steel"
              style={{ width: `${total ? (row.count / total) * 100 : 0}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function Scenario({ dry, mixed, wet }: { dry: number; mixed: number; wet: number }) {
  const parts = [
    { name: "dry", weight: dry, className: "bg-lume/70" },
    { name: "mixed", weight: mixed, className: "bg-lume/30" },
    { name: "wet", weight: wet, className: "bg-wet" },
  ];
  return (
    <div>
      <div className="flex h-2 w-full gap-px">
        {parts.map((part) => (
          <div
            key={part.name}
            className={part.weight > 0 ? part.className : "bg-engrave"}
            style={{ width: `${Math.max(part.weight * 100, part.weight > 0 ? 2 : 1)}%` }}
          />
        ))}
      </div>
      <dl className="mt-3 flex gap-6">
        {parts.map((part) => (
          <div key={part.name}>
            <dt className="engraved">{part.name}</dt>
            <dd className="figure text-sm text-lume">{Math.round(part.weight * 100)}%</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function Neighbours({ rows }: { rows: { circuit_id: string; similarity: number }[] }) {
  return (
    <ul>
      {rows.map((row) => (
        <li
          key={row.circuit_id}
          className="flex items-center gap-3 border-t border-engrave py-1.5"
        >
          <span className="w-28 shrink-0 text-sm tracking-plate text-lume">
            {label(row.circuit_id)}
          </span>
          <span className="relative h-1 flex-1 bg-engrave">
            <span
              className="absolute inset-y-0 left-0 bg-steel"
              style={{ width: `${Math.max(row.similarity, 0) * 100}%` }}
            />
          </span>
          <span className="figure w-12 text-right text-xs text-lume-dim">
            {row.similarity.toFixed(2)}
          </span>
        </li>
      ))}
    </ul>
  );
}
