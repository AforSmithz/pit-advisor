"use client";

import { useEffect, useRef, useState } from "react";

// a chart drawn at a fixed viewBox width takes its viewBox height from the box it sits in, so it fills a short strip or a tall sheet without stretching
export function useFitHeight(width: number, initial: number, min: number, max: number) {
  const box = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState(initial);
  useEffect(() => {
    const node = box.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width: wide, height: tall } = entry!.contentRect;
      if (wide > 0) setHeight(Math.min(max, Math.max(min, Math.round((width * tall) / wide))));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [width, min, max]);
  return [box, height] as const;
}
