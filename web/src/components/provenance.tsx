import Link from "next/link";
import { isoDate } from "@/lib/format";

export function Provenance({
  view,
  runId,
  asOf,
  stands,
}: {
  view: string;
  runId: string;
  asOf: string;
  stands: string;
}) {
  return (
    <footer className="mt-4 flex flex-wrap items-baseline gap-x-6 gap-y-1 border-t border-engrave pt-2">
      <span className="engraved">{stands}</span>
      <span className="figure text-[0.625rem] tracking-wide text-steel">
        {view} · {runId} · as of {isoDate(asOf)}
      </span>
      <Link href="/pipeline/" className="engraved text-lume-dim hover:text-split-lit">
        the gate this run passed
      </Link>
    </footer>
  );
}
