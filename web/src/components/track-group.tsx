"use client";

import { Fragment } from "react";
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
  group?: string;
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
      {rows.map((row, index) => (
        <Fragment key={row.name}>
          {row.group && row.group !== rows[index - 1]?.group && (
            <p className="engraved border-t border-engrave-lit pt-3 pb-1 text-lume-dim">
              {row.group}
            </p>
          )}
          <Track
            name={row.name}
            sub={row.sub}
            estimate={row.estimate}
            missing={row.missing}
            href={row.href}
            scale={scale}
            digits={digits}
            signedValue={signedValue}
          />
        </Fragment>
      ))}
    </ScaleLock>
  );
}
