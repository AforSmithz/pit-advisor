"use client";

import { useEffect, useState } from "react";
import { staleness } from "@/lib/format";

export function Since({ iso }: { iso: string }) {
  const [text, setText] = useState<string | null>(null);
  // rendered at build time, read much later: the age only means anything in the reader's clock
  useEffect(() => {
    const tick = () => setText(staleness(iso, new Date()));
    tick();
    const timer = window.setInterval(tick, 60_000);
    return () => window.clearInterval(timer);
  }, [iso]);
  return <span suppressHydrationWarning>{text ?? `${iso.slice(0, 16).replace("T", " ")}Z`}</span>;
}
