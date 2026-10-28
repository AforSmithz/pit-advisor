"use client";

import { useState } from "react";
import { ELEVATION } from "@/lib/scenery";

const STEPS = [3, 10, 1];

export function ElevationChip() {
  const [step, setStep] = useState(0);
  return (
    <button
      type="button"
      className="pane press inline-flex items-center gap-2 rounded-full px-4 py-2.5 text-sm text-ink-2"
      onClick={() => {
        const next = (step + 1) % STEPS.length;
        setStep(next);
        dispatchEvent(new CustomEvent(ELEVATION, { detail: STEPS[next] }));
      }}
    >
      <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
        <path d="M1.5 13l4.5-7 3 4.5 2-3 3.5 5.5z" stroke="currentColor" strokeWidth="1.4" fill="none" strokeLinejoin="round" />
      </svg>
      Elevation ×{STEPS[step]}
    </button>
  );
}
