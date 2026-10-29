"use client";

import type { LineageView } from "@/lib/schemas";
import type { TraceTarget, ViewStamp } from "./overlays";

const STATUS: Record<string, string> = {
  ok: "#2ee6c5",
  warn: "#ffb35c",
  fail: "#ff5f6d",
  absent: "#55575f",
};

export function TraceDrawer({
  target,
  lineage,
  stamp,
  onClose,
}: {
  target: TraceTarget;
  lineage: LineageView | null;
  stamp: ViewStamp | undefined;
  onClose: () => void;
}) {
  const tables = lineage?.views[target.view] ?? [];
  const traced = lineage ? lineage.tables.filter((item) => tables.includes(item.table)) : [];
  return (
    <div className="fixed inset-0 z-50" role="presentation" onClick={onClose}>
      <aside
        role="dialog"
        aria-label="Where this number comes from"
        onClick={(event) => event.stopPropagation()}
        className="pane absolute top-0 right-0 bottom-0 flex w-full max-w-[480px] flex-col gap-4 overflow-y-auto rounded-l-[34px] p-7"
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <span className="text-[11px] tracking-[0.14em] text-mute uppercase">
              Where this number comes from
            </span>
            <div className="mt-1.5 flex flex-wrap items-baseline gap-3">
              <span className="figure text-[40px] text-sky [text-shadow:0_0_18px_rgba(79,125,255,0.6)]">
                {target.value}
              </span>
              <span className="text-sm text-ink-3">{target.label}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="pane press h-10 w-10 shrink-0 rounded-full text-lg text-ink-3"
          >
            ×
          </button>
        </div>

        <ol className="relative flex flex-col gap-2">
          <span
            aria-hidden="true"
            className="absolute top-4 bottom-4 left-[19px] w-0.5 bg-gradient-to-b from-sky via-aqua to-coral opacity-60"
          />
          <Step color="#7ea2ff" layer="Number" check="shown" name={target.field}>
            The page formats it and does nothing else to it.
          </Step>
          <Step color="#7ea2ff" layer="View" check={stamp ? "gate passed" : "unknown"} name={target.view}>
            {stamp
              ? `Run ${stamp.run_id}, generated ${stamp.generated_at.replace("T", " ").slice(0, 16)} UTC.`
              : "No run recorded for this view."}
          </Step>
          {traced.map((item) => (
            <Step
              key={item.table}
              color={STATUS[item.status] ?? "#55575f"}
              layer={`Bronze, ${item.table}`}
              check={item.status}
              name={[item.rows, item.freshness].filter(Boolean).join(", ") || "not in the lake"}
            >
              {item.source
                ? `Rebuilt from ${item.raw_objects.toLocaleString("en")} raw objects from ${item.source}, kept verbatim and never edited.`
                : "Built from other bronze tables."}
            </Step>
          ))}
        </ol>
        {!lineage && (
          <p className="pane-well rounded-2xl px-4 py-3 text-xs leading-relaxed text-mute">
            The lineage view has not been emitted, so the trace stops at the view.
          </p>
        )}
      </aside>
    </div>
  );
}

function Step({
  color,
  layer,
  check,
  name,
  children,
}: {
  color: string;
  layer: string;
  check: string;
  name: string;
  children: React.ReactNode;
}) {
  return (
    <li className="pane-well relative grid grid-cols-[40px_minmax(0,1fr)] gap-2.5 rounded-2xl py-2.5 pr-3">
      <span
        className="mt-1 h-[13px] w-[13px] justify-self-center rounded-full bg-well"
        style={{ boxShadow: `0 0 0 3px ${color}, 0 0 10px ${color}` }}
      />
      <span className="flex flex-col gap-1">
        <span className="flex justify-between gap-2.5">
          <span className="display text-sm">{layer}</span>
          <span className="figure text-[10.5px]" style={{ color }}>
            {check}
          </span>
        </span>
        <span className="figure text-[11.5px] break-all">{name}</span>
        <span className="text-xs leading-normal text-ink-3">{children}</span>
      </span>
    </li>
  );
}
