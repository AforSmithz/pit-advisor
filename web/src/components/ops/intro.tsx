"use client";

import gsap from "gsap";
import { useLayoutEffect } from "react";
import { SETTLED } from "@/lib/scenery";

const CELL = 18;

function settle(root: HTMLElement) {
  root.removeAttribute("data-ops-intro");
  dispatchEvent(new Event(SETTLED));
}

// title dissolves to the scene, one frosted sheet splits into the cards, parts extrude, wells sink, content last
export function Intro() {
  useLayoutEffect(() => {
    const root = document.querySelector<HTMLElement>("[data-ops-intro]");
    const title = document.querySelector<HTMLElement>("[data-ops-title]");
    if (!root) return;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      title?.remove();
      settle(root);
      return;
    }
    const splits = [...root.querySelectorAll<HTMLElement>("[data-split]")].filter((el) => el.offsetParent !== null);
    const wells = [...root.querySelectorAll<HTMLElement>(".pane-well")];
    const contents = [...root.querySelectorAll<HTMLElement>(".ops-content")];

    let stop = () => {};
    let cancelled = false;
    const start = () => {
      if (cancelled) return;
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
      title?.remove();

      const cols = Math.ceil(innerWidth / CELL), rows = Math.ceil(innerHeight / CELL);
      const order = Float32Array.from({ length: cols * rows }, (_, i) => ((i % cols) / cols) * 0.72 + Math.random() * 0.28);
      const sweep = { t: 0 };

      // the phone layout scrolls, so viewport-fixed blobs would land in the wrong place
      const liquid = innerWidth >= 1024 && splits.length > 0;
      const goo = document.createElement("div");
      Object.assign(goo.style, { position: "fixed", inset: "0", pointerEvents: "none", filter: "url(#ops-goo)", opacity: "0", zIndex: "50" });
      const panels = root.querySelector<HTMLElement>("[data-ops-panels]")?.getBoundingClientRect();
      const blobs = liquid && panels
        ? splits.map((el) => {
            const b = document.createElement("div");
            Object.assign(b.style, {
              position: "absolute", left: `${panels.left}px`, top: `${panels.top}px`,
              width: `${panels.width}px`, height: `${panels.height}px`, background: "#1a1c20", borderRadius: "26px",
            });
            goo.appendChild(b);
            return { b, r: el.getBoundingClientRect() };
          })
        : [];
      document.body.appendChild(goo);

      const tl = gsap.timeline({ delay: 0.4, onComplete: () => goo.remove() });
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
      });
      if (blobs.length) tl.to(goo, { opacity: 0.92, duration: 0.35 }, "-=0.35");
      tl.addLabel("split");
      blobs.forEach(({ b, r }, i) => {
        tl.to(b, { left: r.left, top: r.top, width: r.width, height: r.height, duration: 1.05, ease: "expo.inOut" }, `split+=${i * 0.05}`);
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
        canvas.remove();
        goo.remove();
      };
    };
    // the title is painted to a canvas, so it waits for the display face
    void document.fonts.ready.then(start);

    return () => {
      cancelled = true;
      stop();
    };
  }, []);

  return (
    <svg width="0" height="0" className="absolute" aria-hidden="true">
      <filter id="ops-goo">
        <feGaussianBlur in="SourceGraphic" stdDeviation="12" result="b" />
        <feColorMatrix in="b" values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 22 -9" />
      </filter>
    </svg>
  );
}
