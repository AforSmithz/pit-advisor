"use client";

import { usePathname } from "next/navigation";
import { useEffect, useLayoutEffect, useRef } from "react";
import { type Box, ease, hash, inset, lit, rgba, skyline } from "@/lib/mosaic";

const CELL = 22;
const INTRO = 1560;
const COVER = 260;
const SPLIT = 720;
const WIPE = 460;
const STEP = 40;
const RISE = 620;
const OUT = "cubic-bezier(0.23, 1, 0.32, 1)";
const SPRING = "cubic-bezier(0.34, 1.45, 0.64, 1)";
const WORD = "PIT ADVISOR";
const NOISE = "#/\\<>%&01";
const CARDS = "section.raised, section.glass";
const PAD = 60;
const GOO = "curtain-goo";

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function tokens(name: string): string[] {
  return token(name)
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

// the blob a panel shrinks into before it springs out to fill its card
function drop(box: Box): Box {
  const x = Math.min(34, (box.right - box.left) * 0.14);
  const y = Math.min(34, (box.bottom - box.top) * 0.14);
  return { left: box.left + x, top: box.top + y, right: box.right - x, bottom: box.bottom - y };
}

function screen(): Box {
  return { left: 0, top: 0, right: innerWidth, bottom: innerHeight };
}

function sections(shell: boolean): HTMLElement[] {
  const main = document.querySelector("main");
  const cards = main
    ? [...main.querySelectorAll<HTMLElement>(CARDS)].filter((el) => !el.parentElement?.closest(CARDS))
    : [];
  const chrome = shell ? [...document.querySelectorAll<HTMLElement>("[data-split]")] : [];
  return [...chrome, ...cards].filter((el) => {
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight;
  });
}

type Paint = {
  box: Box;
  front: number[];
  tail: number[] | null;
  fp: number;
  tp: number;
  fill: HTMLCanvasElement | string;
  caps: string[];
  tick: number;
  dpr: number;
  lines: number[];
};

function paint(ctx: CanvasRenderingContext2D, o: Paint) {
  const { box } = o;
  const rows = Math.ceil((box.bottom - box.top) / CELL);
  const cap = (x: number, w: number, c: number, row: number) => {
    ctx.fillStyle = o.caps[Math.floor(hash(c, row, o.tick) * o.caps.length)] ?? "#fff";
    ctx.fillRect(x, box.bottom - (row + 1) * CELL, w, CELL);
  };
  o.front.forEach((lag, c) => {
    const x = box.left + c * CELL;
    const w = Math.min(CELL, box.right - x);
    const hi = lit(rows, o.fp, lag);
    const lo = o.tail ? Math.min(hi, lit(rows, o.tp, o.tail[c] ?? 0)) : 0;
    const y0 = Math.max(box.top, box.bottom - hi * CELL);
    const y1 = box.bottom - lo * CELL;
    if (y1 > y0) {
      if (typeof o.fill === "string") {
        ctx.fillStyle = o.fill;
        ctx.fillRect(x, y0, w, y1 - y0);
      } else {
        ctx.drawImage(o.fill, x * o.dpr, y0 * o.dpr, w * o.dpr, (y1 - y0) * o.dpr, x, y0, w, y1 - y0);
      }
      ctx.fillStyle = "rgba(255, 255, 255, 0.4)";
      for (const y of o.lines) if (y > y0 && y < y1) ctx.fillRect(x, y, w, 1);
    }
    if (o.fp > 0 && hi < rows) {
      cap(x, w, c, hi);
      if (hash(c, hi, 7) < 0.4 && hi + 1 < rows) cap(x, w, c, hi + 1);
    }
    if (o.tail && o.tp > 0 && lo < hi) cap(x, w, c, lo);
  });
}

class Engine {
  private gen = 0;
  private frame = 0;
  private timers: number[] = [];
  private anims: Animation[] = [];
  private phase: "intro" | "leaving" | "idle" = "idle";
  private land: () => void = () => {};
  private landed: Promise<void> = Promise.resolve();
  private still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  private layer: HTMLDivElement;
  private deck: HTMLDivElement;
  private canvas: HTMLCanvasElement;
  private blur: SVGFEGaussianBlurElement | null;
  private label: string;

  constructor(layer: HTMLDivElement, deck: HTMLDivElement, canvas: HTMLCanvasElement, label: string) {
    this.layer = layer;
    this.deck = deck;
    this.canvas = canvas;
    this.label = label;
    this.blur = layer.querySelector("feGaussianBlur");
    document.addEventListener("click", this.onClick, true);
    addEventListener("keydown", this.onKey);
    layer.addEventListener("pointerdown", this.skip);
  }

  stop() {
    document.removeEventListener("click", this.onClick, true);
    removeEventListener("keydown", this.onKey);
    this.layer.removeEventListener("pointerdown", this.skip);
    this.halt();
    this.idle();
  }

  async intro() {
    if (this.still) return this.idle();
    this.halt();
    const gen = this.gen;
    this.phase = "intro";
    this.engage(true);
    const ctx = this.fit();
    const cols = Math.ceil(innerWidth / CELL);
    const front = skyline(cols, Math.random());
    const tail = skyline(cols, Math.random());
    const fill = this.wash();
    const caps = tokens("--mosaic-caps");
    const lines = [0.34, 0.66, 0.81].map((f) => Math.round(innerHeight * f));
    const display = token("--font-saira-condensed");
    const mono = token("--font-chivo-mono");
    void document.fonts.load(`600 64px ${display}`).catch(() => {});
    await this.run(gen, INTRO, (t) => {
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      paint(ctx, {
        box: screen(),
        front,
        tail,
        fp: ease(t / 1100),
        tp: ease((t - 480) / 1020),
        fill,
        caps,
        tick: Math.floor(t / 70),
        dpr: devicePixelRatio,
        lines,
      });
      this.word(ctx, t, display, mono);
    });
    if (gen !== this.gen) return;
    await this.split(screen(), sections(true), gen);
  }

  leave() {
    if (this.still) return;
    this.halt();
    const gen = this.gen;
    this.phase = "leaving";
    this.landed = new Promise<void>((resolve) => (this.land = resolve));
    void this.cover(gen);
  }

  arrived() {
    if (this.still) return;
    if (this.phase !== "leaving") this.leave();
    this.veil(true);
    this.land();
  }

  private onClick = (event: MouseEvent) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = (event.target as Element | null)?.closest?.("a[href]");
    if (!(link instanceof HTMLAnchorElement)) return;
    if ((link.target && link.target !== "_self") || link.hasAttribute("download")) return;
    const url = new URL(link.href, location.href);
    const here = location.pathname.replace(/\/$/, "");
    // feed.xml, calendar.ics and share.png are files, not pages
    if (url.origin !== location.origin || /\.[a-z0-9]+$/i.test(url.pathname)) return;
    if (url.pathname.replace(/\/$/, "") === here) return;
    this.leave();
  };

  private onKey = (event: KeyboardEvent) => {
    if (event.key === "Escape") this.skip();
  };

  private skip = () => {
    if (this.phase === "idle") return;
    this.halt();
    this.idle();
  };

  private async cover(gen: number) {
    const main = document.querySelector("main");
    const left = main ? Math.max(0, main.getBoundingClientRect().left) : 0;
    const box = { left, top: 0, right: innerWidth, bottom: innerHeight };
    this.engage(false);
    const ctx = this.fit();
    const front = skyline(Math.ceil((box.right - box.left) / CELL), Math.random());
    const caps = tokens("--mosaic-caps");
    const black = token("--color-black") || "black";
    await this.run(gen, COVER, (t) => {
      ctx.clearRect(0, 0, innerWidth, innerHeight);
      paint(ctx, {
        box,
        front,
        tail: null,
        fp: ease(t / COVER),
        tp: 0,
        fill: black,
        caps,
        tick: Math.floor(t / 60),
        dpr: devicePixelRatio,
        lines: [],
      });
    });
    if (gen !== this.gen) return;
    await Promise.race([this.landed, this.wait(1500)]);
    if (gen !== this.gen) return;
    // give next a beat to paint and scroll the new page before measuring it
    await this.wait(30);
    if (gen !== this.gen) return;
    await this.split(box, sections(false), gen);
  }

  private async split(from: Box, els: HTMLElement[], gen: number) {
    const w = innerWidth;
    const h = innerHeight;
    const items = els
      .map((el) => {
        const rect = el.getBoundingClientRect();
        return {
          el,
          box: { left: rect.left, top: Math.max(0, rect.top), right: rect.right, bottom: Math.min(h, rect.bottom) },
          radius: Number.parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0,
        };
      })
      .sort((a, b) => a.box.top - b.box.top || a.box.left - b.box.left);

    // panels overhang the screen so the goo never rounds the screen edges
    const wide = w + PAD * 2;
    const tall = h + PAD * 2;
    const clip = (box: Box, radius = 0) =>
      inset({ left: box.left + PAD, top: box.top + PAD, right: box.right + PAD, bottom: box.bottom + PAD }, wide, tall, radius);
    const edge = (n: number, max: number) => (n <= 0 ? -PAD : n >= max ? max + PAD : n);
    const start = { left: edge(from.left, w), top: edge(from.top, h), right: edge(from.right, w), bottom: edge(from.bottom, h) };

    this.deck.style.filter = `url(#${GOO})`;
    // heavy blur keeps the black joined by necks while the panels pull apart, then it sharpens
    void this.run(gen, SPLIT, (t) => {
      const k = t / SPLIT;
      this.blur?.setAttribute("stdDeviation", (k < 0.5 ? 26 - 22 * k : 15 - 16 * (k - 0.5)).toFixed(2));
    });
    this.veil(false);
    this.layer.classList.remove("bg-black");
    this.canvas.getContext("2d")?.clearRect(0, 0, w, h);

    let end = SPLIT;
    const head = document.querySelector("main > header");
    if (head) [...head.children].forEach((child, k) => (end = Math.max(end, this.rise(child, 1, SPLIT * 0.6 + k * 50))));

    items.forEach(({ el, box, radius }, i) => {
      const up = i % 2 === 0;
      const panel = document.createElement("div");
      panel.className = "absolute bg-black";
      panel.style.inset = `-${PAD}px`;
      this.deck.append(panel);
      const whole = clip(box, radius);
      const shut = clip(up ? { ...box, bottom: box.top } : { ...box, top: box.bottom }, radius);
      const at = SPLIT + i * (STEP + 12);
      this.anims.push(
        panel.animate(
          [
            { clipPath: clip(start), easing: "cubic-bezier(0.5, 0, 0.3, 1)" },
            { clipPath: clip(drop(box), radius * 2.2), offset: 0.5, easing: SPRING },
            { clipPath: whole },
          ],
          { duration: SPLIT, delay: i * 12, fill: "both" },
        ),
        panel.animate([{ clipPath: whole }, { clipPath: shut }], {
          duration: WIPE,
          delay: at,
          easing: OUT,
          fill: "forwards",
        }),
      );
      // the panel pulls away and the contents follow it out
      [...el.children].forEach((child, k) => (end = Math.max(end, this.rise(child, up ? -1 : 1, at + 60 + k * 45))));
    });

    this.later(() => this.layer.removeAttribute("data-curtain"), SPLIT);
    await this.wait(end);
    if (gen !== this.gen) return;
    this.anims = [];
    this.idle();
  }

  private rise(el: Element, dir: number, delay: number): number {
    const moved = getComputedStyle(el).translate !== "none";
    this.anims.push(
      el.animate(
        [moved ? { opacity: 0, offset: 0 } : { opacity: 0, translate: `0 ${dir * 26}px`, offset: 0 }],
        { duration: RISE, delay, easing: OUT, fill: "backwards" },
      ),
    );
    return delay + RISE;
  }

  private word(ctx: CanvasRenderingContext2D, t: number, display: string, mono: string) {
    const size = Math.min(128, Math.max(44, innerWidth * 0.1));
    const y = innerHeight / 2 - size * 0.1;
    ctx.font = `600 ${size}px ${display}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = token("--color-ink");
    const chars = [...WORD];
    const widths = chars.map((ch) => ctx.measureText(ch).width);
    const gap = size * 0.06;
    let x = innerWidth / 2 - (widths.reduce((a, b) => a + b, 0) + gap * (chars.length - 1)) / 2;
    chars.forEach((ch, k) => {
      const shown = t - (140 + k * 42);
      const gone = t - (1180 + k * 22);
      const scramble = NOISE[Math.floor(hash(k, Math.floor(t / 50), 3) * NOISE.length)] ?? "";
      const glyph = gone > 0 ? (gone < 90 ? scramble : "") : shown > 120 ? ch : shown > 0 ? scramble : "";
      const slot = widths[k] ?? 0;
      if (glyph.trim()) ctx.fillText(glyph, x + slot / 2, y);
      x += slot + gap;
    });
    const alpha = Math.min(1, Math.max(0, (t - 420) / 260)) * (1 - Math.min(1, Math.max(0, (t - 1150) / 200)));
    if (alpha <= 0) return;
    ctx.globalAlpha = alpha;
    ctx.font = `400 12px ${mono}`;
    if ("letterSpacing" in ctx) ctx.letterSpacing = "0.18em";
    ctx.fillStyle = token("--color-ink-2");
    ctx.fillText(this.label.toUpperCase(), innerWidth / 2, y + size * 0.62);
    if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
    ctx.globalAlpha = 1;
  }

  private wash(): HTMLCanvasElement {
    const dpr = devicePixelRatio;
    const off = document.createElement("canvas");
    off.width = Math.ceil(innerWidth * dpr);
    off.height = Math.ceil(innerHeight * dpr);
    const ctx = off.getContext("2d");
    if (!ctx) return off;
    const stops = tokens("--mosaic");
    const ramp = ctx.createLinearGradient(0, off.height, 0, 0);
    stops.forEach((color, i) => ramp.addColorStop(i / Math.max(1, stops.length - 1), color));
    ctx.fillStyle = ramp;
    ctx.fillRect(0, 0, off.width, off.height);
    const beam = token("--color-violet");
    const glow = ctx.createRadialGradient(off.width * 0.55, off.height, 0, off.width * 0.55, off.height, off.height * 0.7);
    glow.addColorStop(0, rgba(beam, 0.7));
    glow.addColorStop(1, rgba(beam, 0));
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, off.width, off.height);
    return off;
  }

  private fit(): CanvasRenderingContext2D {
    const dpr = devicePixelRatio;
    this.canvas.width = Math.ceil(innerWidth * dpr);
    this.canvas.height = Math.ceil(innerHeight * dpr);
    const ctx = this.canvas.getContext("2d") as CanvasRenderingContext2D;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  private run(gen: number, duration: number, draw: (t: number) => void): Promise<void> {
    return new Promise((resolve) => {
      const start = performance.now();
      const tick = (now: number) => {
        if (gen !== this.gen) return;
        const t = now - start;
        draw(Math.min(t, duration));
        if (t >= duration) resolve();
        else this.frame = requestAnimationFrame(tick);
      };
      this.frame = requestAnimationFrame(tick);
    });
  }

  private wait(ms: number): Promise<void> {
    return new Promise((resolve) => this.later(resolve, ms));
  }

  private later(fn: () => void, ms: number) {
    this.timers.push(window.setTimeout(fn, ms));
  }

  private engage(black: boolean) {
    this.layer.setAttribute("data-curtain", "");
    this.layer.classList.toggle("bg-black", black);
    this.layer.style.pointerEvents = "auto";
  }

  private veil(on: boolean) {
    const main = document.querySelector("main");
    if (main) main.style.visibility = on ? "hidden" : "";
  }

  private halt() {
    this.gen++;
    cancelAnimationFrame(this.frame);
    for (const timer of this.timers) clearTimeout(timer);
    this.timers = [];
    for (const anim of this.anims) anim.cancel();
    this.anims = [];
  }

  private idle() {
    this.phase = "idle";
    this.deck.replaceChildren();
    this.deck.style.filter = "";
    this.blur?.setAttribute("stdDeviation", "7");
    this.layer.removeAttribute("data-curtain");
    this.layer.classList.remove("bg-black");
    this.layer.style.pointerEvents = "none";
    this.canvas.getContext("2d")?.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.veil(false);
  }
}

export function Curtain({ label }: { label: string }) {
  const layer = useRef<HTMLDivElement>(null);
  const deck = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const engine = useRef<Engine | null>(null);
  const path = usePathname();
  const seen = useRef(path);

  useEffect(() => {
    if (!layer.current || !deck.current || !canvas.current) return;
    const run = new Engine(layer.current, deck.current, canvas.current, label);
    engine.current = run;
    void run.intro();
    return () => run.stop();
  }, [label]);

  useLayoutEffect(() => {
    if (seen.current === path) return;
    seen.current = path;
    engine.current?.arrived();
  }, [path]);

  return (
    <div ref={layer} data-curtain="" aria-hidden="true" className="fixed inset-0 z-[70] bg-black">
      <svg width="0" height="0" className="absolute">
        <filter id={GOO} x="-10%" y="-10%" width="120%" height="120%" colorInterpolationFilters="sRGB">
          <feGaussianBlur in="SourceGraphic" stdDeviation="7" />
          <feColorMatrix values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 24 -11" />
        </filter>
      </svg>
      <div ref={deck} className="absolute inset-0" />
      <canvas ref={canvas} className="absolute inset-0 h-full w-full" />
    </div>
  );
}
