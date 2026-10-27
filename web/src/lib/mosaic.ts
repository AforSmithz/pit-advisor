export type Box = { left: number; top: number; right: number; bottom: number };

export function hash(a: number, b: number, c: number): number {
  let h = Math.imul(a ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul(b + 0x632be5ab, 0xc2b2ae35) ^ Math.imul(c + 0x2c1b3c6d, 0x27d4eb2f);
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d);
  h ^= h >>> 12;
  return (h >>> 0) / 4294967296;
}

// when each cell lets go, 0 to 1, a ragged front that sweeps left to right
export function dissolve(cols: number, rows: number, seed: number): Float32Array {
  const a = seed * 6.283;
  const b = seed * 17.3;
  const out = new Float32Array(cols * rows);
  for (let c = 0; c < cols; c++) {
    const x = c / Math.max(1, cols - 1);
    const lump = 0.5 + 0.32 * Math.sin(c * 0.11 + a) + 0.18 * Math.sin(c * 0.29 + b);
    for (let r = 0; r < rows; r++) out[r * cols + c] = 0.68 * x + 0.16 * lump + 0.16 * hash(c, r, 11);
  }
  return out;
}

export function bounds(boxes: Box[]): Box {
  return {
    left: Math.min(...boxes.map((box) => box.left)),
    top: Math.min(...boxes.map((box) => box.top)),
    right: Math.max(...boxes.map((box) => box.right)),
    bottom: Math.max(...boxes.map((box) => box.bottom)),
  };
}

export function clip(box: Box, within: Box): Box {
  const left = Math.min(Math.max(box.left, within.left), within.right);
  const top = Math.min(Math.max(box.top, within.top), within.bottom);
  return {
    left,
    top,
    right: Math.max(left, Math.min(box.right, within.right)),
    bottom: Math.max(top, Math.min(box.bottom, within.bottom)),
  };
}

// a card grown into its share of the gaps, so the cards together tile the slab
export function grown(box: Box, by: number, slab: Box, snap = 28): Box {
  const pick = (value: number, edge: number) => (Math.abs(value - edge) <= snap ? edge : value);
  const out = clip({ left: box.left - by, top: box.top - by, right: box.right + by, bottom: box.bottom + by }, slab);
  return {
    left: pick(out.left, slab.left),
    top: pick(out.top, slab.top),
    right: pick(out.right, slab.right),
    bottom: pick(out.bottom, slab.bottom),
  };
}

// a card shrunk and pushed away from the slab centre, the far point of the pull
export function pulled(box: Box, from: { x: number; y: number }, shrink = 0.1, push = 26, most = 44): Box {
  const w = box.right - box.left;
  const h = box.bottom - box.top;
  const sx = Math.min(most, w * shrink);
  const sy = Math.min(most, h * shrink);
  const dx = (box.left + box.right) / 2 - from.x;
  const dy = (box.top + box.bottom) / 2 - from.y;
  const len = Math.hypot(dx, dy) || 1;
  const ox = (dx / len) * push;
  const oy = (dy / len) * push;
  return { left: box.left + sx + ox, top: box.top + sy + oy, right: box.right - sx + ox, bottom: box.bottom - sy + oy };
}

export type Seam = { a: number; b: number; axis: "x" | "y" };

// pairs of cards that face each other across a gap, a is left of or above b
export function seams(boxes: Box[], gap = 40, overlap = 48): Seam[] {
  const out: Seam[] = [];
  boxes.forEach((a, i) =>
    boxes.forEach((b, j) => {
      if (i === j) return;
      const across = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      const down = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      if (b.left - a.right >= -2 && b.left - a.right <= gap && across >= overlap) out.push({ a: i, b: j, axis: "x" });
      if (b.top - a.bottom >= -2 && b.top - a.bottom <= gap && down >= overlap) out.push({ a: i, b: j, axis: "y" });
    }),
  );
  return out;
}

// the same shadow list with every length at zero, the flat surface it grows out of
export function flat(shadow: string): string {
  return shadow.replace(/-?\d*\.?\d+px/g, "0px");
}
