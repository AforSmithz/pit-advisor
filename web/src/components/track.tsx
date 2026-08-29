"use client";

import { position, type Scale } from "@/lib/scale";
import type { Estimate } from "@/lib/schemas";
import { plain, signed } from "@/lib/format";
import { useScaleLock } from "./scale-lock";

export function Ruler({ scale, unit }: { scale: Scale; unit: string }) {
  const lock = useScaleLock();
  return (
    <div className="grid grid-cols-[9rem_1fr_5.5rem] items-end gap-x-4">
      <div className="engraved pb-1">{unit}</div>
      <div className="relative h-6">
        <div className="absolute inset-x-0 bottom-0 h-px bg-engrave-lit" />
        {scale.ticks.map((tick, index) => (
          <div
            key={tick}
            className="tick absolute bottom-0 flex -translate-x-1/2 flex-col items-center"
            data-odd={index % 2 === 1 ? "" : undefined}
            style={{ left: `${position(tick, scale)}%` }}
          >
            <span className="figure numeral mb-1 text-[0.625rem] leading-none text-steel">
              {plain(tick, Math.abs(tick) < 1 && tick !== 0 ? 2 : 1)}
            </span>
            <span className={`w-px ${tick === 0 ? "h-3 bg-steel" : "h-1.5 bg-engrave-lit"}`} />
          </div>
        ))}
        {lock.at !== null && (
          <div
            className="absolute bottom-0 h-4 w-px -translate-x-1/2 bg-split-lit"
            style={{ left: `${position(lock.at, scale)}%` }}
          >
            <span className="absolute -top-1 left-1/2 block h-1.5 w-1.5 -translate-x-1/2 rotate-45 bg-split-lit" />
          </div>
        )}
      </div>
      <div className="engraved pb-1 text-right">n</div>
    </div>
  );
}

type TrackProps = {
  name: string;
  sub?: string;
  estimate: Estimate | null;
  scale: Scale;
  missing?: string;
  digits?: number;
  href?: string;
  signedValue?: boolean;
};

export function Track({
  name,
  sub,
  estimate,
  scale,
  missing,
  digits = 2,
  href,
  signedValue = true,
}: TrackProps) {
  const lock = useScaleLock();
  const locked = lock.pinned && lock.from === name;

  if (!estimate) {
    return (
      <div className="grid grid-cols-[9rem_1fr_5.5rem] items-center gap-x-4 border-t border-engrave py-2">
        <TrackName name={name} sub={sub} href={href} />
        <div className="cut-face flex h-7 items-center px-3">
          <span className="engraved text-lume-dim">{missing ?? "no fit"}</span>
        </div>
        <div className="figure text-right text-sm text-steel">—</div>
      </div>
    );
  }

  const left = position(estimate.low, scale);
  const right = position(estimate.high, scale);
  const mark = position(estimate.value, scale);

  return (
    <div
      className="group grid cursor-crosshair grid-cols-[9rem_1fr_5.5rem] items-center gap-x-4 border-t border-engrave py-2 outline-offset-4 hover:bg-subplate"
      tabIndex={0}
      role="button"
      aria-pressed={locked}
      aria-label={`${name} ${estimate.value.toFixed(digits)}, interval ${estimate.low.toFixed(digits)} to ${estimate.high.toFixed(digits)}, ${estimate.samples} samples`}
      onMouseEnter={() => lock.hover(estimate.value, name)}
      onMouseLeave={() => lock.hover(null, null)}
      onFocus={() => lock.hover(estimate.value, name)}
      onBlur={() => lock.hover(null, null)}
      onClick={() => lock.toggle(estimate.value, name)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          lock.toggle(estimate.value, name);
        }
      }}
    >
      <TrackName name={name} sub={sub} href={href} />
      <div className="relative h-7">
        <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-engrave" />
        {scale.ticks.map((tick) => (
          <span
            key={tick}
            className={`absolute top-1/2 w-px -translate-x-1/2 -translate-y-1/2 ${
              tick === 0 ? "h-7 bg-engrave-lit" : "h-2 bg-engrave"
            }`}
            style={{ left: `${position(tick, scale)}%` }}
          />
        ))}
        <div
          className="absolute top-1/2 h-2 -translate-y-1/2 bg-lume/15"
          style={{ left: `${left}%`, width: `${Math.max(right - left, 0.35)}%` }}
        />
        <span
          className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-lume/60"
          style={{ left: `${left}%` }}
        />
        <span
          className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-lume/60"
          style={{ left: `${right}%` }}
        />
        <span
          className={`absolute top-1/2 w-0.5 -translate-x-1/2 -translate-y-1/2 bg-split transition-[height] duration-150 ease-out ${
            locked ? "h-7" : "h-5 group-hover:h-6"
          }`}
          style={{ left: `${mark}%` }}
        />
        {lock.at !== null && !locked && (
          <span
            className="pointer-events-none absolute top-1/2 h-6 w-px -translate-x-1/2 -translate-y-1/2 bg-split-lit/45"
            style={{ left: `${position(lock.at, scale)}%` }}
          />
        )}
      </div>
      <div className="text-right">
        <span className="figure text-sm text-lume">
          {signedValue ? signed(estimate.value, digits) : plain(estimate.value, digits)}
        </span>
        <span className="engraved ml-2 tabular-nums">{estimate.samples}</span>
      </div>
    </div>
  );
}

function TrackName({ name, sub, href }: { name: string; sub?: string; href?: string }) {
  const body = (
    <>
      <span className="block text-sm tracking-plate text-lume">{name}</span>
      {sub && <span className="engraved block">{sub}</span>}
    </>
  );
  return href ? (
    <a href={href} className="block hover:text-split-lit">
      {body}
    </a>
  ) : (
    <div>{body}</div>
  );
}
