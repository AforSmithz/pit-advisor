"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

function Glyph({ d }: { d: string }) {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
      <path d={d} stroke="currentColor" strokeWidth="1.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// a card that shows a short or squeezed version in the column and opens the whole thing as a sheet over the scene
export function Expand({
  label,
  peek,
  height,
  className = "",
  children,
}: {
  label: string;
  peek?: number;
  height?: number;
  className?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    addEventListener("keydown", onKey);
    return () => removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <section data-split className="pane flex flex-col gap-2 rounded-3xl px-5 pt-4 pb-2.5">
      <div
        className={`flex min-h-0 flex-col gap-2.5 ${peek ? "overflow-hidden" : ""} ${className}`}
        style={
          peek
            ? { maxHeight: peek, maskImage: "linear-gradient(to bottom, black 72%, transparent)" }
            : height
              ? { height }
              : undefined
        }
      >
        {children}
      </div>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="press ml-auto inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs text-mute hover:text-ink"
      >
        {label}
        <Glyph d="M9.5 2.5h4v4M13.5 2.5L9 7M6.5 13.5h-4v-4M2.5 13.5L7 9" />
      </button>
      {open &&
        createPortal(
          <div
            role="dialog"
            aria-modal="true"
            aria-label={label}
            className="pane sheet fixed inset-x-3 top-3 bottom-[88px] z-40 flex flex-col gap-2 rounded-3xl px-6 pt-3 pb-5 lg:inset-y-5 lg:right-[112px] lg:bottom-[30px] lg:left-5"
          >
            <button
              type="button"
              onClick={() => setOpen(false)}
              autoFocus
              className="press ml-auto inline-flex items-center gap-1.5 rounded-full px-2.5 py-1.5 text-xs text-mute hover:text-ink"
            >
              Close
              <Glyph d="M4 4l8 8M12 4l-8 8" />
            </button>
            <div className={`flex min-h-0 flex-1 flex-col gap-3 overflow-auto ${className}`}>{children}</div>
          </div>,
          document.body,
        )}
    </section>
  );
}
