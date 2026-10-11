"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { LineageView } from "@/lib/schemas";
import { AskPalette } from "./ask-palette";
import { ShareSheet } from "./share-sheet";
import { TraceDrawer } from "./trace-drawer";

export type ViewStamp = { run_id: string; generated_at: string };

export type TraceTarget = {
  kind: "trace";
  view: string;
  label: string;
  value: string;
  field: string;
};

type Overlay = { kind: "ask" } | { kind: "share"; tab?: "share" | "follow" } | TraceTarget;

type Context = {
  open: (overlay: Overlay) => void;
  close: () => void;
};

const OverlayContext = createContext<Context>({ open: () => {}, close: () => {} });

export function useOverlay(): Context {
  return useContext(OverlayContext);
}

export function Overlays({
  children,
  lineage,
  stamps,
  share,
}: {
  children: React.ReactNode;
  lineage: LineageView | null;
  stamps: Record<string, ViewStamp>;
  share: { title: string; race: string };
}) {
  const [current, setCurrent] = useState<Overlay | null>(null);
  const close = useCallback(() => setCurrent(null), []);
  const context = useMemo(() => ({ open: setCurrent, close }), [close]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCurrent((now) => (now?.kind === "ask" ? null : { kind: "ask" }));
      }
      if (event.key === "Escape") setCurrent(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <OverlayContext.Provider value={context}>
      {children}
      {current?.kind === "ask" && <AskPalette onClose={close} />}
      {current?.kind === "share" && (
        <ShareSheet onClose={close} initial={current.tab ?? "share"} {...share} />
      )}
      {current?.kind === "trace" && (
        <TraceDrawer target={current} lineage={lineage} stamp={stamps[current.view]} onClose={close} />
      )}
    </OverlayContext.Provider>
  );
}

/** A figure that opens its trace when pressed. The figure itself is passed in already formatted. */
export function Traced({
  view,
  label,
  field,
  children,
  className = "",
}: {
  view: string;
  label: string;
  field: string;
  children: string;
  className?: string;
}) {
  const { open } = useOverlay();
  return (
    <button
      type="button"
      onClick={() => open({ kind: "trace", view, label, value: children, field })}
      className={`cursor-pointer rounded-md underline decoration-dotted decoration-1 underline-offset-4 hover:decoration-solid ${className}`}
      aria-label={`${label}: ${children}. Show where this number comes from`}
    >
      {children}
    </button>
  );
}
