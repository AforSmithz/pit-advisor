"use client";

import gsap from "gsap";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useLayoutEffect, useRef } from "react";
import { type Box, bounds, clip, dissolve, flat, grown, hash, pulled, seams } from "@/lib/mosaic";

const CELL = 14;
const PAD = 60;
const GOO = "curtain-goo";
const WORD = "PIT ADVISOR";
const NOISE = "#/\\<>%&01";
const LIFE = 0.42;
const CARDS = "section.raised, section.glass";
const PARTS = ".raised, .raised-sm, .inset, .inset-sm, .glass, .pill-on, .pill-cool";
const SUNK = ".inset, .inset-sm";
const GLOW = ".glass, .pill-on, .pill-cool";
const KEEP = ["box-shadow", "background-color", "opacity", "transform", "translate", "scale", "rotate", "transition"];
const ARC = "M3.5 13a6.5 6.5 0 1 1 13 0";

type Shape = { x: number; y: number; w: number; h: number; r: number };
type Piece = { el: HTMLDivElement; s: Shape };
type Strand = { el: HTMLDivElement; a: Shape; b: Shape; axis: "x" | "y"; f: number; k: { w: number } };
type Styled = HTMLElement | SVGElement;

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function tokens(name: string): string[] {
  return token(name)
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

function unit(n: number): number {
  return Math.min(1, Math.max(0, n));
}

function shape(box: Box, r: number): Shape {
  return { x: box.left, y: box.top, w: box.right - box.left, h: box.bottom - box.top, r };
}

function box(s: Shape): Box {
  return { left: s.x, top: s.y, right: s.x + s.w, bottom: s.y + s.h };
}

function place(el: HTMLElement, s: Shape) {
  el.style.transform = `translate(${s.x + PAD}px, ${s.y + PAD}px)`;
  el.style.width = `${Math.max(0, s.w)}px`;
  el.style.height = `${Math.max(0, s.h)}px`;
  el.style.borderRadius = `${Math.max(0, s.r)}px`;
}

function measure(el: Element): Box {
  const rect = el.getBoundingClientRect();
  // cards running off screen overhang it, so the goo never rounds a corner nobody can see
  return clip(rect, { left: -PAD, top: -PAD, right: innerWidth + PAD, bottom: innerHeight + PAD });
}

function sections(): HTMLElement[] {
  const main = document.querySelector("main");
  if (!main) return [];
  return [...main.querySelectorAll<HTMLElement>(CARDS)].filter((el) => {
    if (el.parentElement?.closest(CARDS)) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.top < innerHeight;
  });
}

// the biggest pieces of a card that hold no surface of their own: text, charts, marks
function leaves(root: Element, out: Styled[] = []): Styled[] {
  for (const child of root.children) {
    if (!(child instanceof HTMLElement || child instanceof SVGElement)) continue;
    if (child.matches(PARTS) || child.querySelector(PARTS)) leaves(child, out);
    else out.push(child);
  }
  return out;
}

function depthOf(el: Element, card: Element): number {
  let n = 0;
  for (let up = el.parentElement; up && up !== card; up = up.parentElement) if (up.matches(PARTS)) n++;
  return n;
}

function copy(source: HTMLCanvasElement): HTMLCanvasElement {
  const out = document.createElement("canvas");
  out.width = source.width;
  out.height = source.height;
  out.getContext("2d")?.drawImage(source, 0, 0);
  return out;
}

class Engine {
  private tl: gsap.core.Timeline | null = null;
  private phase: "idle" | "playing" | "leaving" | "slab" = "idle";
  private slab: Piece | null = null;
  private pieces: Piece[] = [];
  private strands: Strand[] = [];
  private undo: (() => void)[] = [];
  private goo = { blur: 0 };
  private stray = 0;
  private still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  private layer: HTMLDivElement;
  private deck: HTMLDivElement;
  private canvas: HTMLCanvasElement;
  private blur: SVGFEGaussianBlurElement | null;
  private label: string;
  private go: (href: string) => void;

  constructor(
    layer: HTMLDivElement,
    deck: HTMLDivElement,
    canvas: HTMLCanvasElement,
    label: string,
    go: (href: string) => void,
  ) {
    this.layer = layer;
    this.deck = deck;
    this.canvas = canvas;
    this.label = label;
    this.go = go;
    this.blur = layer.querySelector("feGaussianBlur");
    document.addEventListener("click", this.onClick, true);
    addEventListener("keydown", this.onKey);
    addEventListener("resize", this.skip);
    layer.addEventListener("pointerdown", this.skip);
  }

  stop() {
    document.removeEventListener("click", this.onClick, true);
    removeEventListener("keydown", this.onKey);
    removeEventListener("resize", this.skip);
    this.layer.removeEventListener("pointerdown", this.skip);
    this.halt();
    this.finish();
  }

  intro() {
    if (this.still) return this.finish();
    this.veil(true);
    const tl = this.start("playing");
    this.layer.classList.add("bg-ground");
    const ctx = this.fit();
    this.title(tl, ctx);
    this.dissolve(tl, ctx);
    this.split(tl, null);
  }

  arrived() {
    if (this.still) return;
    const from = this.phase === "slab" || this.phase === "leaving" ? this.slab && { ...this.slab.s } : null;
    this.veil(true);
    // a frame for next to scroll the new page into place before it gets measured
    requestAnimationFrame(() => {
      const tl = this.start("playing");
      this.split(tl, from);
    });
  }

  private leave(href: string) {
    const cards = sections();
    if (this.still || cards.length === 0) return this.go(href);
    const tl = this.start("leaving");
    const boxes = cards.map(measure);
    const slab = bounds(boxes);
    const slabTone = token("--slab");

    cards.forEach((card) => {
      const parts = [...card.querySelectorAll<HTMLElement>(PARTS)];
      const content = leaves(card);
      [card, ...parts, ...content].forEach((el) => this.hold(el));
      if (content.length) tl.to(content, { opacity: 0, duration: 0.16, ease: "power1.in" }, 0);
      tl.to(
        [card, ...parts],
        { boxShadow: (_: number, el: Element) => flat(getComputedStyle(el).boxShadow), duration: 0.24, ease: "power2.in" },
        0.06,
      );
      const sunk = parts.filter((el) => el.matches(SUNK));
      if (sunk.length) tl.to(sunk, { backgroundColor: slabTone, duration: 0.24, ease: "power2.in" }, 0.06);
      const glow = parts.filter((el) => el.matches(GLOW));
      if (glow.length) tl.to(glow, { opacity: 0, duration: 0.2, ease: "power2.in" }, 0.06);
    });

    this.deck.style.opacity = "0";
    this.deck.style.filter = `url(#${GOO})`;
    this.goo.blur = 0;
    this.slab = this.piece({ x: (slab.left + slab.right) / 2, y: (slab.top + slab.bottom) / 2, w: 0, h: 0, r: 0 });
    this.pieces = boxes.map((b, i) => this.piece(shape(b, radius(cards[i]!))));
    this.draw();

    // the flattened cards are now indistinguishable from the slab pieces, so swap them over
    tl.to(this.deck, { opacity: 1, duration: 0.12, ease: "none" }, 0.2);
    tl.call(
      () => {
        this.veil(true);
        for (const undo of this.undo.splice(0)) undo();
      },
      undefined,
      0.32,
    );
    tl.addLabel("merge", 0.32);
    tl.to(this.goo, { blur: 12, duration: 0.16, ease: "power1.out" }, "merge");
    this.pieces.forEach((p, i) =>
      tl.to(p.s, { ...shape(grown(boxes[i]!, 9, slab), 10), duration: 0.38, ease: "power3.inOut" }, "merge"),
    );
    tl.to(this.slab.s, { ...shape(slab, 30), duration: 0.38, ease: "power2.out" }, "merge+=0.04");
    tl.call(() => {
      this.phase = "slab";
      this.go(href);
      // a navigation that never lands still gets its slab split back open
      this.stray = window.setTimeout(() => this.phase === "slab" && this.arrived(), 2500);
    });
  }

  private title(tl: gsap.core.Timeline, ctx: CanvasRenderingContext2D) {
    const display = token("--font-saira-condensed");
    const mono = token("--font-chivo-mono");
    const ground = token("--color-ground");
    void document.fonts.load(`600 64px ${display}`).catch(() => {});
    const clock = { t: 0 };
    tl.to(clock, {
      t: 1500,
      duration: 1.5,
      ease: "none",
      onUpdate: () => {
        ctx.fillStyle = ground;
        ctx.fillRect(0, 0, innerWidth, innerHeight);
        this.word(ctx, clock.t, display, mono);
      },
    });
  }

  private dissolve(tl: gsap.core.Timeline, ctx: CanvasRenderingContext2D) {
    const dpr = devicePixelRatio;
    const cols = Math.ceil(innerWidth / CELL);
    const rows = Math.ceil(innerHeight / CELL);
    const at = dissolve(cols, rows, Math.random());
    const order = Array.from({ length: at.length }, (_, i) => i).sort((i, j) => (at[i] ?? 0) - (at[j] ?? 0));
    const caps = tokens("--mosaic-caps");
    const side = CELL * dpr;
    const live: number[] = [];
    const clock = { p: 0 };
    let source: HTMLCanvasElement | null = null;
    let rest: HTMLCanvasElement | null = null;
    let next = 0;

    tl.call(() => {
      source = copy(this.canvas);
      rest = copy(this.canvas);
      this.layer.classList.remove("bg-ground");
    });
    tl.to(clock, {
      p: 1 + LIFE,
      duration: 1.3,
      ease: "none",
      onUpdate: () => {
        const left = rest?.getContext("2d");
        if (!source || !rest || !left) return;
        while (next < order.length && (at[order[next]!] ?? 0) <= clock.p) {
          const i = order[next++]!;
          left.clearRect(Math.floor((i % cols) * side), Math.floor(Math.floor(i / cols) * side), Math.ceil(side) + 1, Math.ceil(side) + 1);
          live.push(i);
        }
        ctx.clearRect(0, 0, innerWidth, innerHeight);
        ctx.drawImage(rest, 0, 0, innerWidth, innerHeight);
        let kept = 0;
        for (const i of live) {
          const age = (clock.p - (at[i] ?? 0)) / LIFE;
          if (age >= 1) continue;
          live[kept++] = i;
          const c = i % cols;
          const r = Math.floor(i / cols);
          const drift = 1 - (1 - age) ** 2;
          const size = CELL * (1 - 0.65 * age);
          const x = c * CELL + (24 + 90 * hash(c, r, 1)) * drift + (CELL - size) / 2;
          const y = r * CELL - (12 + 70 * hash(c, r, 2)) * drift + (CELL - size) / 2;
          ctx.globalAlpha = 1 - age;
          if (age < 0.22 && hash(c, r, 3) < 0.45) {
            ctx.fillStyle = caps[Math.floor(hash(c, r, 4) * caps.length)] ?? "#fff";
            ctx.fillRect(x, y, size, size);
          } else {
            ctx.drawImage(source, c * side, r * side, side, side, x, y, size, size);
          }
        }
        live.length = kept;
        ctx.globalAlpha = 1;
      },
    });
    tl.call(() => ctx.clearRect(0, 0, innerWidth, innerHeight));
  }

  private split(tl: gsap.core.Timeline, from: Shape | null) {
    const cards = sections();
    if (cards.length === 0) {
      tl.call(() => this.veil(false));
      return;
    }
    const boxes = cards.map(measure);
    const radii = cards.map(radius);
    const slab = bounds(boxes);
    const centre = { x: (slab.left + slab.right) / 2, y: (slab.top + slab.bottom) / 2 };
    const start = from ? box(from) : slab;

    this.deck.replaceChildren();
    this.deck.style.opacity = "";
    this.deck.style.filter = `url(#${GOO})`;
    this.goo.blur = 12;
    this.slab = this.piece(from ?? shape(slab, 30));
    this.pieces = boxes.map((b) => this.piece(shape(clip(grown(b, 9, slab), start), 10)));
    this.strands = seams(boxes).flatMap((seam) =>
      Array.from({ length: Math.random() < 0.5 ? 3 : 2 }, () => ({
        el: this.blob("var(--slab)"),
        a: this.pieces[seam.a]!.s,
        b: this.pieces[seam.b]!.s,
        axis: seam.axis,
        f: 0.15 + Math.random() * 0.7,
        k: { w: 20 + Math.random() * 14 },
      })),
    );
    this.draw();

    tl.addLabel("morph");
    if (from) {
      tl.to(this.slab.s, { ...shape(slab, 30), duration: 0.3, ease: "power2.inOut" }, "morph");
      this.pieces.forEach((p, i) =>
        tl.to(p.s, { ...shape(grown(boxes[i]!, 9, slab), 10), duration: 0.3, ease: "power2.inOut" }, "morph"),
      );
    }

    // the slab drains into the cards while they pull apart, necks stretch, snap, then the cards spring home
    tl.addLabel("pull");
    tl.to(this.slab.s, { x: centre.x, y: centre.y, w: 0, h: 0, r: 0, duration: 0.55, ease: "power2.in" }, "pull");
    this.pieces.forEach((p, i) =>
      tl.to(
        p.s,
        {
          keyframes: [
            { ...shape(pulled(boxes[i]!, centre), 48), duration: 0.5, ease: "power1.inOut" },
            { ...shape(boxes[i]!, radii[i]!), duration: 0.8, ease: "elastic.out(1, 0.5)" },
          ],
        },
        `pull+=${i * 0.035}`,
      ),
    );
    for (const strand of this.strands) {
      tl.to(strand.k, { w: 0, duration: 0.24 + Math.random() * 0.16, ease: "power3.in" }, `pull+=${0.22 + Math.random() * 0.3}`);
    }
    tl.to(this.goo, { blur: 0, duration: 0.45, ease: "power1.in" }, "pull+=0.7");

    // the slab pieces are flat --slab, the cards a faint gradient, so the pieces fade off them rather than cut
    const settled = (tl.labels.pull ?? 0) + 0.5 + 0.8 + (this.pieces.length - 1) * 0.035;
    tl.call(() => this.veil(false), undefined, settled);
    tl.to(this.deck, { opacity: 0, duration: 0.3, ease: "power1.out" }, settled);
    tl.call(() => this.clear(), undefined, settled + 0.3);
    this.depth(tl, cards, settled);
  }

  // every surface grows out of the flat card: raised parts lift first, outer before inner, then the wells sink
  private depth(tl: gsap.core.Timeline, cards: HTMLElement[], at: number) {
    const slabTone = token("--slab");
    let reveal = Number.POSITIVE_INFINITY;
    cards.forEach((card, i) => {
      const t0 = i * 0.07;
      const parts = [...card.querySelectorAll<HTMLElement>(PARTS)];
      const up = parts.filter((el) => !el.matches(SUNK));
      const down = parts.filter((el) => el.matches(SUNK));
      const content = leaves(card);
      [card, ...parts, ...content].forEach((el) => this.hold(el));

      this.rise(tl, card, false, 0.5, at + t0);
      let lifted = t0 + 0.22;
      up.forEach((el, j) => {
        const t = t0 + 0.2 + depthOf(el, card) * 0.14 + j * 0.012;
        this.rise(tl, el, el.matches(GLOW), 0.42, at + t);
        lifted = Math.max(lifted, t + 0.24);
      });
      let sunk = lifted;
      down.forEach((el, j) => {
        const t = lifted + depthOf(el, card) * 0.08 + j * 0.012;
        const style = getComputedStyle(el);
        tl.fromTo(
          el,
          { boxShadow: flat(style.boxShadow), backgroundColor: slabTone },
          { boxShadow: style.boxShadow, backgroundColor: style.backgroundColor, duration: 0.45, ease: "power2.out" },
          at + t,
        );
        sunk = Math.max(sunk, t + 0.26);
      });
      reveal = Math.min(reveal, sunk);
      if (content.length) {
        tl.fromTo(
          content,
          { opacity: 0 },
          { opacity: 1, duration: 0.4, ease: "power1.out", stagger: Math.min(0.025, 0.4 / content.length) },
          at + sunk,
        );
      }
    });
    // ignition waits for this, so the rings draw and the cells light as the content arrives
    tl.call(() => this.layer.removeAttribute("data-curtain"), undefined, at + reveal);
  }

  private rise(tl: gsap.core.Timeline, el: HTMLElement, fade: boolean, duration: number, at: number) {
    const style = getComputedStyle(el);
    const from: gsap.TweenVars = {};
    const to: gsap.TweenVars = { duration, ease: "power3.out" };
    if (style.boxShadow !== "none") {
      from.boxShadow = flat(style.boxShadow);
      to.boxShadow = style.boxShadow;
    }
    if (fade) {
      from.opacity = 0;
      from.scale = 0.94;
      to.opacity = Number(style.opacity);
      to.scale = 1;
    }
    tl.fromTo(el, from, to, at);
  }

  private onClick = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = (event.target as Element | null)?.closest?.("a[href]");
    if (!(link instanceof HTMLAnchorElement)) return;
    if ((link.target && link.target !== "_self") || link.hasAttribute("download")) return;
    const url = new URL(link.href, location.href);
    // feed.xml, calendar.ics and share.png are files, not pages
    if (url.origin !== location.origin || /\.[a-z0-9]+$/i.test(url.pathname)) return;
    if (url.pathname.replace(/\/$/, "") === location.pathname.replace(/\/$/, "")) return;
    if (this.still || this.phase !== "idle") return;
    // next's link backs off when the click is already handled, so the page waits for the cards to melt
    event.preventDefault();
    this.leave(url.pathname + url.search + url.hash);
  };

  private onKey = (event: KeyboardEvent) => {
    if (event.key === "Escape") this.skip();
  };

  private skip = () => {
    if (this.phase === "playing" || this.phase === "leaving") this.tl?.progress(1, false);
  };

  private start(phase: "playing" | "leaving"): gsap.core.Timeline {
    this.halt();
    this.phase = phase;
    this.layer.setAttribute("data-curtain", "");
    this.layer.style.pointerEvents = "auto";
    const tl = gsap.timeline({ onUpdate: this.draw, onComplete: phase === "playing" ? this.finish : undefined });
    this.tl = tl;
    return tl;
  }

  private draw = () => {
    this.blur?.setAttribute("stdDeviation", this.goo.blur.toFixed(2));
    if (this.slab) place(this.slab.el, this.slab.s);
    for (const p of this.pieces) place(p.el, p.s);
    for (const strand of this.strands) {
      const { a, b, k } = strand;
      if (strand.axis === "x") {
        const top = Math.max(a.y, b.y);
        const y = top + (Math.min(a.y + a.h, b.y + b.h) - top) * strand.f;
        const x = a.x + a.w - 8;
        place(strand.el, { x, y: y - k.w / 2, w: b.x + 8 - x, h: k.w, r: k.w / 2 });
      } else {
        const left = Math.max(a.x, b.x);
        const x = left + (Math.min(a.x + a.w, b.x + b.w) - left) * strand.f;
        const y = a.y + a.h - 8;
        place(strand.el, { x: x - k.w / 2, y, w: k.w, h: b.y + 8 - y, r: k.w / 2 });
      }
    }
  };

  private piece(s: Shape): Piece {
    return { el: this.blob("var(--slab)"), s: { ...s } };
  }

  private blob(fill: string): HTMLDivElement {
    const el = document.createElement("div");
    el.className = "absolute top-0 left-0";
    el.style.background = fill;
    this.deck.append(el);
    return el;
  }

  // gsap writes inline styles, this puts back whatever react had there
  private hold(el: Styled) {
    const saved = KEEP.map((prop) => el.style.getPropertyValue(prop));
    el.style.transition = "none";
    this.undo.push(() => {
      gsap.killTweensOf(el);
      KEEP.forEach((prop, i) => el.style.setProperty(prop, saved[i] ?? ""));
    });
  }

  private word(ctx: CanvasRenderingContext2D, t: number, display: string, mono: string) {
    const size = Math.min(120, Math.max(44, innerWidth * 0.09));
    const y = innerHeight / 2 + size * 0.2;
    const ink = token("--color-ink");
    this.mark(ctx, t, innerWidth / 2, y - size * 1.05, size * 0.09, ink);
    ctx.font = `600 ${size}px ${display}`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillStyle = ink;
    const chars = [...WORD];
    const widths = chars.map((ch) => ctx.measureText(ch).width);
    const gap = size * 0.06;
    let x = innerWidth / 2 - (widths.reduce((a, b) => a + b, 0) + gap * (chars.length - 1)) / 2;
    chars.forEach((ch, k) => {
      const shown = t - (260 + k * 42);
      const scramble = NOISE[Math.floor(hash(k, Math.floor(t / 50), 3) * NOISE.length)] ?? "";
      const glyph = shown > 120 ? ch : shown > 0 ? scramble : "";
      const slot = widths[k] ?? 0;
      if (glyph.trim()) ctx.fillText(glyph, x + slot / 2, y);
      x += slot + gap;
    });
    const alpha = unit((t - 640) / 260);
    if (alpha <= 0) return;
    ctx.globalAlpha = alpha;
    ctx.font = `400 12px ${mono}`;
    if ("letterSpacing" in ctx) ctx.letterSpacing = "0.18em";
    ctx.fillStyle = token("--color-ink-2");
    ctx.fillText(this.label.toUpperCase(), innerWidth / 2, y + size * 0.62);
    if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
    ctx.globalAlpha = 1;
  }

  // the sidebar mark, 20 units square: the dial draws round and the needle swings up to rest
  private mark(ctx: CanvasRenderingContext2D, t: number, cx: number, cy: number, scale: number, ink: string) {
    const arc = gsap.parseEase("power2.out")(unit(t / 520));
    const swing = gsap.parseEase("back.out(2.2)")(unit((t - 200) / 640));
    const angle = Math.PI + (Math.PI - 0.866) * swing;
    ctx.save();
    ctx.translate(cx - 10 * scale, cy - 10 * scale);
    ctx.scale(scale, scale);
    ctx.lineWidth = 1.8;
    ctx.lineCap = "round";
    ctx.strokeStyle = token("--color-signal");
    ctx.setLineDash([20.5 * arc, 40]);
    ctx.stroke(new Path2D(ARC));
    ctx.setLineDash([]);
    if (t > 200) {
      ctx.strokeStyle = ink;
      ctx.beginPath();
      ctx.moveTo(10, 13);
      ctx.lineTo(10 + 5.25 * Math.cos(angle), 13 + 5.25 * Math.sin(angle));
      ctx.stroke();
      ctx.fillStyle = ink;
      ctx.beginPath();
      ctx.arc(10, 13, 1.4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  private fit(): CanvasRenderingContext2D {
    const dpr = devicePixelRatio;
    this.canvas.width = Math.ceil(innerWidth * dpr);
    this.canvas.height = Math.ceil(innerHeight * dpr);
    const ctx = this.canvas.getContext("2d") as CanvasRenderingContext2D;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
  }

  private veil(on: boolean) {
    document.documentElement.toggleAttribute("data-veil", on);
  }

  private clear() {
    this.deck.replaceChildren();
    this.deck.style.filter = "";
    this.deck.style.opacity = "";
    this.slab = null;
    this.pieces = [];
    this.strands = [];
  }

  private halt() {
    clearTimeout(this.stray);
    this.tl?.kill();
    this.tl = null;
    for (const undo of this.undo.splice(0)) undo();
    this.canvas.getContext("2d")?.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.layer.classList.remove("bg-ground");
  }

  private finish = () => {
    this.phase = "idle";
    this.tl = null;
    for (const undo of this.undo.splice(0)) undo();
    this.clear();
    this.canvas.getContext("2d")?.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.layer.removeAttribute("data-curtain");
    this.layer.classList.remove("bg-ground");
    this.layer.style.pointerEvents = "none";
    this.veil(false);
  };
}

function radius(el: Element): number {
  return Number.parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0;
}

export function Curtain({ label }: { label: string }) {
  const layer = useRef<HTMLDivElement>(null);
  const deck = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const engine = useRef<Engine | null>(null);
  const router = useRouter();
  const path = usePathname();
  const seen = useRef(path);

  useEffect(() => {
    if (!layer.current || !deck.current || !canvas.current) return;
    const run = new Engine(layer.current, deck.current, canvas.current, label, (href) => router.push(href));
    engine.current = run;
    run.intro();
    return () => run.stop();
  }, [label, router]);

  useLayoutEffect(() => {
    if (seen.current === path) return;
    seen.current = path;
    engine.current?.arrived();
  }, [path]);

  return (
    <div ref={layer} data-curtain="" aria-hidden="true" className="fixed inset-0 z-[70] bg-ground">
      <svg width="0" height="0" className="absolute">
        <filter id={GOO} x="-10%" y="-10%" width="120%" height="120%" colorInterpolationFilters="sRGB">
          <feGaussianBlur in="SourceGraphic" stdDeviation="12" />
          <feColorMatrix values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 24 -11" />
        </filter>
      </svg>
      <div ref={deck} className="absolute" style={{ inset: -PAD }} />
      <canvas ref={canvas} className="absolute inset-0 h-full w-full" />
    </div>
  );
}
