"use client";

import gsap from "gsap";
import { useLayoutEffect, useRef } from "react";
import { SETTLED } from "@/lib/scenery";

const CELL = 18;

function settle(root: HTMLElement) {
  root.removeAttribute("data-ops-intro");
  dispatchEvent(new Event(SETTLED));
}

function onScreen(r: DOMRect): boolean {
  return r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth;
}

// keeps only the outermost of nested matches, so a fade is never applied twice
function outermost(list: HTMLElement[]): HTMLElement[] {
  const set = new Set(list);
  return list.filter((el) => {
    for (let p = el.parentElement; p; p = p.parentElement) if (set.has(p)) return false;
    return true;
  });
}

function dissolve(title: HTMLElement): { canvas: HTMLCanvasElement; tween: (tl: gsap.core.Timeline) => void } {
  const canvas = document.createElement("canvas");
  Object.assign(canvas.style, { position: "fixed", inset: "0", width: "100%", height: "100%", pointerEvents: "none", zIndex: "60" });
  const dpr = Math.min(devicePixelRatio, 2);
  canvas.width = innerWidth * dpr;
  canvas.height = innerHeight * dpr;
  document.body.appendChild(canvas);
  const g = canvas.getContext("2d")!;
  g.scale(dpr, dpr);
  g.fillStyle = "#0b0c0e";
  g.fillRect(0, 0, innerWidth, innerHeight);
  g.fillStyle = "#ececef";
  g.font = `500 44px ${getComputedStyle(document.body).getPropertyValue("--font-saira-condensed") || "sans-serif"}`;
  g.textAlign = "center";
  g.letterSpacing = "6px";
  g.fillText("PIT ADVISOR", innerWidth / 2, innerHeight / 2 + 14);
  g.fillStyle = "#ff8a4c";
  g.fillRect(innerWidth / 2 - 1, innerHeight / 2 - 58, 2, 30);
  title.hidden = true;

  const cols = Math.ceil(innerWidth / CELL), rows = Math.ceil(innerHeight / CELL);
  const order = Float32Array.from({ length: cols * rows }, (_, i) => ((i % cols) / cols) * 0.72 + Math.random() * 0.28);
  const sweep = { t: 0 };
  return {
    canvas,
    tween: (tl) =>
      tl.to(sweep, {
        t: 1,
        duration: 1.1,
        ease: "power1.inOut",
        onUpdate() {
          for (let i = 0; i < order.length; i++) {
            if (order[i]! < sweep.t) g.clearRect((i % cols) * CELL, Math.floor(i / cols) * CELL, CELL, CELL);
          }
        },
        onComplete: () => canvas.remove(),
      }),
  };
}

// first load: the title dissolves to the scene. every page: one frosted sheet splits into the cards, parts extrude, wells sink, content last
export function Intro() {
  const ref = useRef<SVGSVGElement>(null);

  useLayoutEffect(() => {
    const root = ref.current?.closest<HTMLElement>("[data-ops-intro]");
    const title = document.querySelector<HTMLElement>("[data-ops-title]");
    const first = title !== null && !title.hidden;
    if (!root) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      if (title) title.hidden = true;
      settle(root);
      return;
    }
    const splits = [...root.querySelectorAll<HTMLElement>("[data-split]")].filter((el) => el.offsetParent !== null);
    const wells = [...root.querySelectorAll<HTMLElement>(".pane-well")];
    const contents = outermost([
      ...root.querySelectorAll<HTMLElement>("[data-split] > :not(.pane-well), [data-split] .pane-well > *, .ops-content"),
    ]);

    let stop = () => {};
    let cancelled = false;
    const start = () => {
      if (cancelled) return;
      const intro = first && title ? dissolve(title) : null;

      // the phone layout scrolls, so viewport-fixed blobs would land in the wrong place
      const seen = innerWidth >= 1024 ? splits.map((el) => ({ el, r: el.getBoundingClientRect() })).filter(({ r }) => onScreen(r)) : [];
      const column = root.querySelector<HTMLElement>("[data-ops-panels]")?.getBoundingClientRect();
      const sheet = column && seen.length ? { l: column.left, t: column.top, r: column.right, b: column.bottom } : seen.reduce<{ l: number; t: number; r: number; b: number } | null>(
        (u, { r }) => (u ? { l: Math.min(u.l, r.left), t: Math.min(u.t, r.top), r: Math.max(u.r, r.right), b: Math.max(u.b, r.bottom) } : { l: r.left, t: r.top, r: r.right, b: r.bottom }),
        null,
      );
      const goo = document.createElement("div");
      Object.assign(goo.style, { position: "fixed", inset: "0", pointerEvents: "none", filter: "url(#ops-goo)", opacity: "0", zIndex: "50" });
      const blobs = sheet
        ? seen.map(({ r }) => {
            const b = document.createElement("div");
            Object.assign(b.style, {
              position: "absolute", left: `${sheet.l}px`, top: `${sheet.t}px`,
              width: `${sheet.r - sheet.l}px`, height: `${sheet.b - sheet.t}px`, background: "#1a1c20", borderRadius: "26px",
            });
            goo.appendChild(b);
            return { b, r };
          })
        : [];
      document.body.appendChild(goo);

      const tl = gsap.timeline({ delay: first ? 0.4 : 0.05, onComplete: () => goo.remove() });
      if (intro) intro.tween(tl);
      if (blobs.length) tl.to(goo, { opacity: 0.92, duration: 0.35 }, first ? "-=0.35" : 0);
      tl.addLabel("split");
      blobs.forEach(({ b, r }, i) => {
        tl.to(b, { left: r.left, top: r.top, width: r.width, height: r.height, duration: first ? 1.05 : 0.85, ease: "expo.inOut" }, `split+=${i * 0.05}`);
      });
      tl.addLabel("settled");
      tl.add(() => settle(root), "settled");
      if (blobs.length) {
        tl.set(splits, { opacity: 1 }, "settled");
        tl.to(goo, { opacity: 0, duration: 0.35 }, "settled");
      } else {
        tl.to(splits, { opacity: 1, duration: 0.3, stagger: 0.04 }, "settled");
      }
      tl.fromTo(splits, { "--lift": 0 }, { "--lift": 1, duration: 0.6, ease: "power2.out", stagger: 0.04 }, "settled+=0.1");
      tl.fromTo(wells, { "--sink": 0 }, { "--sink": 1, duration: 0.5, ease: "power2.out", stagger: 0.02 }, ">-0.2");
      tl.fromTo(contents, { opacity: 0 }, { opacity: 1, duration: 0.45, stagger: 0.015 }, ">-0.15");
      stop = () => {
        tl.kill();
        intro?.canvas.remove();
        goo.remove();
      };
    };
    // the title is painted to a canvas, so it waits for the display face
    if (first) void document.fonts.ready.then(start);
    else start();

    return () => {
      cancelled = true;
      stop();
    };
  }, []);

  return (
    <svg ref={ref} width="0" height="0" className="absolute" aria-hidden="true">
      <filter id="ops-goo">
        <feGaussianBlur in="SourceGraphic" stdDeviation="12" result="b" />
        <feColorMatrix in="b" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 22 -9" />
      </filter>
    </svg>
  );
}
