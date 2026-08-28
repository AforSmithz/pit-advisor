"use client";

import { Ruler, Track } from "./track";
import { ScaleLock } from "./scale-lock";
import { scaleFor } from "@/lib/scale";
import type { Estimate } from "@/lib/schemas";

export type Row = {
  name: string;
  sub?: string;
  estimate: Estimate | null;
  missing?: string;
  href?: string;
};

export function TrackGroup({
  rows,
  unit,
  digits = 2,
  signedValue = true,
}: {
  rows: Row[];
  unit: string;
  digits?: number;
  signedValue?: boolean;
}) {
  const scale = scaleFor(rows.map((row) => row.estimate));
  return (
    <ScaleLock>
      <Ruler scale={scale} unit={unit} />
      {rows.map((row) => (
        <Track
          key={row.name}
          name={row.name}
          sub={row.sub}
          estimate={row.estimate}
          missing={row.missing}
          href={row.href}
          scale={scale}
          digits={digits}
          signedValue={signedValue}
        />
      ))}
    </ScaleLock>
  );
}
