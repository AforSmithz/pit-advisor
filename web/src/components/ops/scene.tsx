"use client";

import gsap from "gsap";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";
import { sceneryView, type SceneryView } from "@/lib/schemas";
import { ELEVATION, SETTLED, type Pose, framing, perimeter, poseFor, ribbon, sectorIndices, shot, type Vec3, world } from "@/lib/scenery";

// drawn wider and deeper than the real tarmac so the slab still reads from the overview camera
const HALF = 10;
const DEPTH = 12;
const GROUND = 0x0b0c0e;

const PAINT: Record<string, string> = {
  urban: "#1e2128",
  grass: "#162e28",
  pitch: "#1a382c",
  wood: "#122620",
  parking: "#24272e",
  water: "#102a44",
  pool: "#143c5c",
};
const PAINT_ORDER = ["urban", "grass", "pitch", "wood", "parking", "water", "pool"];

function paintGround(view: SceneryView): HTMLCanvasElement {
  const [minx, miny, maxx, maxy] = view.plinth.box;
  const width = 2048;
  const height = Math.round((width * (maxy - miny)) / (maxx - minx));
  const scale = width / (maxx - minx);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const g = canvas.getContext("2d")!;
  const trace = (flat: number[]) => {
    g.beginPath();
    for (let k = 0; k < flat.length; k += 2) {
      const px = (flat[k]! - minx) * scale, py = (maxy - flat[k + 1]!) * scale;
      if (k === 0) g.moveTo(px, py);
      else g.lineTo(px, py);
    }
  };
  g.fillStyle = "#191b21";
  g.fillRect(0, 0, width, height);
  for (const kind of PAINT_ORDER) {
    g.fillStyle = PAINT[kind]!;
    for (const area of view.areas) {
      if (area.kind !== kind) continue;
      for (const ring of area.rings) {
        trace(ring);
        g.fill();
      }
    }
  }
  g.lineCap = g.lineJoin = "round";
  for (const road of [...view.roads].sort((a, b) => a.width_m - b.width_m)) {
    g.strokeStyle = road.kind === "rail" ? "#343842" : road.width_m >= 6 ? "#2c3039" : "#24272e";
    g.lineWidth = Math.max(1, road.width_m * scale);
    trace(road.points);
    g.stroke();
  }
  g.strokeStyle = "rgb(255 172 92 / 0.6)";
  for (const road of view.roads) {
    if (!road.lit) continue;
    g.lineWidth = Math.max(2, 3.5 * scale);
    trace(road.points);
    g.stroke();
  }
  return canvas;
}

function surface(positions: number[], index: number[]): THREE.BufferGeometry {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  if (index.length) g.setIndex(index);
  g.computeVertexNormals();
  return g;
}

function lines(positions: number[], opacity: number): THREE.LineSegments {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  return new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity }));
}

function label(text: string, size: number): THREE.Sprite {
  const c = document.createElement("canvas");
  c.width = 128;
  c.height = 64;
  const g = c.getContext("2d")!;
  g.font = `500 44px ${getComputedStyle(document.body).getPropertyValue("--font-saira-condensed") || "sans-serif"}`;
  g.fillStyle = "#ececef";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(text, 64, 34);
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }),
  );
  sprite.scale.set(size, size / 2, 1);
  return sprite;
}

function buildTrack(view: SceneryView, exaggerate: number): { group: THREE.Group; anchor: THREE.Vector3 } {
  const group = new THREE.Group();
  const { points } = view.track;
  const n = points.length;
  const { centre, left, right } = ribbon(points, HALF, DEPTH, exaggerate);
  const top: number[] = [], topIndex: number[] = [], side: number[] = [], sideIndex: number[] = [];
  const edge: number[] = [], curtain: number[] = [];
  const at = (list: Vec3[], i: number): Vec3 => list[i % n]!;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const l = at(left, i), r = at(right, i), c = at(centre, i);
    top.push(...l, ...r);
    topIndex.push(2 * i, 2 * j, 2 * i + 1, 2 * i + 1, 2 * j, 2 * j + 1);
    for (const e of [l, r]) side.push(...e, e[0], e[1] - DEPTH, e[2]);
    const a = 4 * i, b = 4 * j;
    sideIndex.push(a, a + 1, b, a + 1, b + 1, b, a + 2, b + 2, a + 3, a + 3, b + 2, b + 3);
    for (const [u, v] of [[l, at(left, j)], [r, at(right, j)]] as const) edge.push(u[0], u[1] + 0.3, u[2], v[0], v[1] + 0.3, v[2]);
    if (i % 8 === 0 && c[1] - DEPTH > 2) curtain.push(c[0], 0, c[2], c[0], c[1] - DEPTH, c[2]);
  }
  group.add(new THREE.Mesh(surface(top, topIndex), new THREE.MeshStandardMaterial({ color: 0xd3d8df, roughness: 0.55, metalness: 0.05, side: THREE.DoubleSide })));
  group.add(new THREE.Mesh(surface(side, sideIndex), new THREE.MeshStandardMaterial({ color: 0x30353d, roughness: 0.85, side: THREE.DoubleSide })));
  group.add(lines(edge, 0.6));
  if (curtain.length) group.add(lines(curtain, 0.07));

  const span = new THREE.Box3().setFromPoints(centre.map((p) => new THREE.Vector3(...p))).getSize(new THREE.Vector3()).length();
  const gateMaterial = new THREE.MeshBasicMaterial({ color: 0xececef });
  const lift = (p: Vec3, by: number) => new THREE.Vector3(p[0], p[1] + by, p[2]);
  sectorIndices(view.track).forEach((i, k) => {
    const gate = new THREE.Mesh(new THREE.BoxGeometry(2 * HALF + 10, 4, 3), gateMaterial);
    gate.position.copy(lift(at(centre, i), 2));
    const along = new THREE.Vector3(...at(centre, i + 1)).sub(new THREE.Vector3(...at(centre, i)));
    gate.lookAt(gate.position.clone().add(along));
    group.add(gate);
    const tag = label(`S${k + 2}`, span * 0.07);
    tag.position.copy(lift(at(centre, i), span * 0.035));
    group.add(tag);
  });
  const first = label("S1", span * 0.07);
  first.position.copy(lift(at(centre, 0), span * 0.035)).add(new THREE.Vector3(span * 0.03, 0, 0));
  group.add(first);

  const post = new THREE.Mesh(new THREE.CylinderGeometry(5, 5, 120, 16), new THREE.MeshBasicMaterial({ color: 0xff8a4c }));
  post.position.copy(lift(at(centre, 0), 60));
  group.add(post);
  return { group, anchor: lift(at(centre, 0), 120) };
}

const buildingMaterial = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.85, metalness: 0 });
buildingMaterial.onBeforeCompile = (shader) => {
  shader.vertexShader = shader.vertexShader
    .replace(
      "#include <common>",
      "#include <common>\nattribute float kind;\nattribute float seed;\nattribute vec2 wuv;\nvarying float vKind;\nvarying float vSeed;\nvarying vec2 vW;\nvarying float vWall;",
    )
    .replace("#include <begin_vertex>", "#include <begin_vertex>\nvKind = kind; vSeed = seed; vW = wuv; vWall = 1.0 - abs(normal.y);");
  shader.fragmentShader = shader.fragmentShader
    .replace("#include <common>", "#include <common>\nvarying float vKind;\nvarying float vSeed;\nvarying vec2 vW;\nvarying float vWall;")
    .replace(
      "#include <color_fragment>",
      `#include <color_fragment>
  float isWall = step(0.5, vWall);
  float stand = step(0.5, vKind) * step(vKind, 1.5);
  float canopy = step(1.5, vKind);
  vec3 wallTone = mix(vec3(0.085, 0.095, 0.125), vec3(0.2, 0.21, 0.25), stand);
  vec3 roofTone = mix(vec3(0.055, 0.062, 0.08), vec3(0.16, 0.17, 0.2), stand + canopy);
  diffuseColor.rgb = mix(roofTone, wallTone, isWall);
  float storey = 1.0 - vW.y;
  vec2 cell = vec2(floor(vW.x / 9.0), floor(storey / 3.1));
  float band = fract(storey / 3.1);
  float window = step(0.38, band) * step(band, 0.72) * step(0.6, storey) * isWall * (1.0 - stand) * (1.0 - canopy);
  float lit = step(0.55, fract(sin(dot(cell + vSeed * 31.7, vec2(12.9898, 78.233))) * 43758.5453));`,
    )
    .replace("#include <emissivemap_fragment>", "#include <emissivemap_fragment>\n  totalEmissiveRadiance += vec3(1.0, 0.68, 0.36) * window * lit * 2.2;");
};

const KIND: Record<string, number> = { building: 0, grandstand: 1, canopy: 2 };

function buildDiorama(view: SceneryView, ground: THREE.CanvasTexture, exaggerate: number): { group: THREE.Group; base: number } {
  const group = new THREE.Group();
  const [minx, miny, maxx, maxy] = view.plinth.box;
  const [gx, gy] = view.plinth.grid;
  const top = new THREE.PlaneGeometry(maxx - minx, maxy - miny, gx - 1, gy - 1);
  top.rotateX(-Math.PI / 2);
  const pos = top.getAttribute("position") as THREE.BufferAttribute;
  // plane rows run north to south, the height grid runs south to north
  for (let r = 0; r < gy; r++) for (let c = 0; c < gx; c++) pos.setY(r * gx + c, view.plinth.heights[(gy - 1 - r) * gx + c]! * exaggerate);
  top.translate((minx + maxx) / 2, 0, -(miny + maxy) / 2);
  top.computeVertexNormals();
  group.add(new THREE.Mesh(top, new THREE.MeshStandardMaterial({ map: ground, roughness: 0.95, metalness: 0 })));

  let lowest = Infinity;
  for (let i = 0; i < pos.count; i++) lowest = Math.min(lowest, pos.getY(i));
  const base = lowest - 90;
  const ring = perimeter(gx, gy);
  const wall: number[] = [], rim: number[] = [];
  ring.forEach((a, k) => {
    const b = ring[(k + 1) % ring.length]!;
    const ax = pos.getX(a), ay = pos.getY(a), az = pos.getZ(a), bx = pos.getX(b), by = pos.getY(b), bz = pos.getZ(b);
    wall.push(ax, ay, az, ax, base, az, bx, by, bz, bx, by, bz, ax, base, az, bx, base, bz);
    rim.push(ax, ay + 0.4, az, bx, by + 0.4, bz);
  });
  group.add(new THREE.Mesh(surface(wall, []), new THREE.MeshStandardMaterial({ color: 0x15171c, roughness: 1, side: THREE.DoubleSide })));
  group.add(lines(rim, 0.35));

  if (view.buildings.length) {
    const parts = view.buildings.map((b, i) => {
      const shape = new THREE.Shape();
      for (let k = 0; k < b.points.length; k += 2) {
        if (k === 0) shape.moveTo(b.points[k]!, b.points[k + 1]!);
        else shape.lineTo(b.points[k]!, b.points[k + 1]!);
      }
      const g = new THREE.ExtrudeGeometry(shape, { depth: b.h, bevelEnabled: false });
      g.rotateX(-Math.PI / 2);
      g.translate(0, b.base * exaggerate, 0);
      const count = g.getAttribute("position").count;
      g.setAttribute("kind", new THREE.Float32BufferAttribute(new Float32Array(count).fill(KIND[b.kind] ?? 0), 1));
      g.setAttribute("seed", new THREE.Float32BufferAttribute(new Float32Array(count).fill((i * 0.6180339) % 1), 1));
      g.setAttribute("wuv", g.getAttribute("uv").clone());
      return g;
    });
    const merged = mergeGeometries(parts);
    parts.forEach((g) => g.dispose());
    if (merged) {
      group.add(new THREE.Mesh(merged, buildingMaterial));
      group.add(new THREE.LineSegments(new THREE.EdgesGeometry(merged, 35), new THREE.LineBasicMaterial({ color: 0xa8b6cc, transparent: true, opacity: 0.16 })));
    }
  }

  const count = view.trees.length / 3;
  if (count) {
    const crowns = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1, 0), new THREE.MeshStandardMaterial({ roughness: 0.9, flatShading: true }), count);
    const m = new THREE.Matrix4(), q = new THREE.Quaternion(), up = new THREE.Vector3(0, 1, 0), at = new THREE.Vector3(), scale = new THREE.Vector3(), colour = new THREE.Color();
    for (let i = 0; i < count; i++) {
      const r = 2.6 + ((i * 0.7548776) % 1) * 2.4;
      const [x, height, z] = world(view.trees[3 * i]!, view.trees[3 * i + 1]!, view.trees[3 * i + 2]! * exaggerate + r * 1.3);
      crowns.setMatrixAt(i, m.compose(at.set(x, height, z), q.setFromAxisAngle(up, i), scale.set(r, r * 1.35, r)));
      crowns.setColorAt(i, colour.setHSL(0.45 + ((i * 0.37) % 1) * 0.05, 0.55, 0.2 + ((i * 0.53) % 1) * 0.08));
    }
    group.add(crowns);
  }
  return { group, base };
}

const floorMaterial = new THREE.ShaderMaterial({
  uniforms: { bg: { value: new THREE.Color(GROUND) }, fogNear: { value: 2000 }, fogFar: { value: 6000 } },
  vertexShader: `
    varying vec3 vWorld;
    varying float vDepth;
    void main() {
      vec4 w = modelMatrix * vec4(position, 1.0);
      vWorld = w.xyz;
      vec4 v = viewMatrix * w;
      vDepth = -v.z;
      gl_Position = projectionMatrix * v;
    }`,
  fragmentShader: `
    uniform vec3 bg;
    uniform float fogNear, fogFar;
    varying vec3 vWorld;
    varying float vDepth;
    void main() {
      vec2 cell = abs(fract(vWorld.xz / 100.0 + 0.5) - 0.5) * 100.0;
      float dots = 1.0 - smoothstep(1.4, 1.4 + fwidth(vWorld.x) * 1.5, length(cell));
      vec3 col = bg + vec3(1.0) * dots * 0.07;
      gl_FragColor = vec4(mix(col, bg, smoothstep(fogNear, fogFar, vDepth)), 1.0);
    }`,
});

export function Scene({ circuitId, children }: { circuitId: string; children: React.ReactNode }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const anchorRef = useRef<HTMLDivElement>(null);
  const lineRef = useRef<SVGLineElement>(null);
  const leaderRef = useRef<SVGSVGElement>(null);
  const dotRef = useRef<SVGCircleElement>(null);
  const [missing, setMissing] = useState(false);
  const pose = poseFor(usePathname());
  const poseRef = useRef(pose);
  const goRef = useRef<(pose: Pose) => void>(() => {});

  useEffect(() => {
    poseRef.current = pose;
    goRef.current(pose);
  }, [pose]);

  useEffect(() => {
    const canvas = canvasRef.current!;
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(GROUND);
    scene.fog = new THREE.Fog(GROUND, 1800, 5200);
    const camera = new THREE.PerspectiveCamera(36, 1, 1, 20000);
    const composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    // only the lit floors and the brightest edge of the slab clear this threshold
    const bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.55, 0.45, 0.8);
    composer.addPass(bloom);
    composer.addPass(new OutputPass());
    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.autoRotateSpeed = 0.25;
    controls.maxPolarAngle = Math.PI * 0.46;
    controls.minDistance = 300;
    scene.add(new THREE.HemisphereLight(0xdfe6ff, 0x101114, 1.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(-800, 1600, 600);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0x9fb4ff, 0.35);
    fill.position.set(900, 500, -700);
    scene.add(fill);
    const floor = new THREE.Mesh(new THREE.PlaneGeometry(40000, 40000).rotateX(-Math.PI / 2), floorMaterial);
    scene.add(floor);

    let dirty = true;
    let view: SceneryView | null = null;
    let ground: THREE.CanvasTexture | null = null;
    let exaggerate = 3;
    let track: THREE.Group | null = null;
    let diorama: THREE.Group | null = null;
    let anchor: THREE.Vector3 | null = null;
    let frame = framing([[0, 0, 0], [1000, 1000, 0]], 1);
    let settled = reduce;
    let eased = false;
    let raf = 0;
    // every page leaves room for the panel column on the right
    const offset = { x: 0.17 };

    const dispose = (root: THREE.Object3D | null) => {
      if (!root) return;
      scene.remove(root);
      root.traverse((o) => {
        const mesh = o as THREE.Mesh;
        mesh.geometry?.dispose();
        for (const m of [mesh.material].flat()) {
          if (m && m !== buildingMaterial) {
            (m as THREE.SpriteMaterial).map?.dispose();
            m.dispose();
          }
        }
      });
    };

    const rebuild = () => {
      if (!view || !ground) return;
      dispose(track);
      dispose(diorama);
      const built = buildTrack(view, exaggerate);
      track = built.group;
      anchor = built.anchor;
      scene.add(track);
      const plinth = buildDiorama(view, ground, exaggerate);
      diorama = plinth.group;
      scene.add(diorama);
      floor.position.y = plinth.base - 0.5;
      dirty = true;
    };

    const resize = () => {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      renderer.setSize(w, h, false);
      composer.setSize(w, h);
      camera.aspect = w / h;
      const wide = innerWidth >= 1024;
      camera.setViewOffset(w, h, wide ? Math.round(w * offset.x) : 0, Math.round(h * (wide ? 0.04 : 0.2)), w, h);
      camera.updateProjectionMatrix();
      dirty = true;
    };

    const placeCamera = () => {
      if (!view) return;
      frame = framing(view.track.points, innerWidth / innerHeight);
      scene.fog = new THREE.Fog(GROUND, frame.distance * 0.8, frame.distance * 2.4);
      floorMaterial.uniforms.fogNear!.value = frame.distance * 0.8;
      floorMaterial.uniforms.fogFar!.value = frame.distance * 2.4;
      controls.maxDistance = frame.distance * 1.6;
      if (settled) {
        const { position, target } = shot(poseRef.current, view.track, frame, exaggerate);
        controls.target.set(...target);
        camera.position.set(...position);
      } else {
        controls.target.set(...frame.centre);
        camera.position.set(...frame.overhead);
      }
      eased = settled;
      dirty = true;
    };

    const showAnchor = (on: boolean, delay: number) => {
      const el = anchorRef.current;
      if (!el) return;
      el.style.pointerEvents = on ? "" : "none";
      gsap.to([el, leaderRef.current], { opacity: on ? 1 : 0, duration: reduce ? 0 : 0.4, delay: on ? delay : 0, overwrite: true });
    };

    const fly = (to: Pose, duration: number) => {
      if (!view) return;
      const { position, target } = shot(to, view.track, frame, exaggerate);
      const moved = () => { dirty = true; };
      controls.autoRotate = to === "drift" && !reduce;
      gsap.to(camera.position, { x: position[0], y: position[1], z: position[2], duration, ease: "power3.inOut", onUpdate: moved, overwrite: true });
      gsap.to(controls.target, { x: target[0], y: target[1], z: target[2], duration, ease: "power3.inOut", onUpdate: moved, overwrite: true });
      showAnchor(to === "overview", duration * 0.75);
    };

    const ease = () => {
      if (!view || eased) return;
      eased = true;
      fly(poseRef.current, 1.6);
    };

    goRef.current = (to) => {
      if (settled && eased) fly(to, reduce ? 0 : 1.4);
    };

    const onSettled = () => {
      settled = true;
      ease();
    };
    const onElevation = (e: Event) => {
      exaggerate = (e as CustomEvent<number>).detail;
      rebuild();
    };

    const place = () => {
      const el = anchorRef.current, line = lineRef.current, dot = dotRef.current;
      if (!anchor || !el || !line || !dot) return;
      const v = anchor.clone().project(camera);
      const sx = (v.x * 0.5 + 0.5) * canvas.clientWidth, sy = (-v.y * 0.5 + 0.5) * canvas.clientHeight;
      const left = sx - 300 >= 24;
      const ax = left ? sx - 300 : sx + 56, ay = Math.max(170, sy - 190);
      el.style.transform = `translate(${ax}px, ${ay}px)`;
      line.setAttribute("x1", String(left ? ax + el.offsetWidth : ax));
      line.setAttribute("y1", String(ay + 60));
      line.setAttribute("x2", String(sx));
      line.setAttribute("y2", String(sy));
      dot.setAttribute("cx", String(sx));
      dot.setAttribute("cy", String(sy));
    };

    const loop = () => {
      raf = requestAnimationFrame(loop);
      const moving = controls.update();
      if (dirty || moving) {
        composer.render();
        place();
        dirty = false;
      }
    };

    const onResize = () => {
      resize();
      placeCamera();
    };

    resize();
    controls.addEventListener("change", () => { dirty = true; });
    addEventListener("resize", onResize);
    addEventListener(SETTLED, onSettled);
    addEventListener(ELEVATION, onElevation);
    if (!reduce || poseRef.current !== "overview") for (const el of [anchorRef.current, leaderRef.current]) el?.style.setProperty("opacity", "0");
    loop();

    const abort = new AbortController();
    fetch(`/data/scenery/${circuitId}.json`, { signal: abort.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(async (json: unknown) => {
        const parsed = sceneryView.safeParse(json);
        if (!parsed.success) throw new Error(parsed.error.message);
        await document.fonts.ready;
        view = parsed.data;
        ground = new THREE.CanvasTexture(paintGround(view));
        ground.colorSpace = THREE.SRGBColorSpace;
        ground.anisotropy = renderer.capabilities.getMaxAnisotropy();
        rebuild();
        placeCamera();
        if (settled) ease();
      })
      .catch((err: unknown) => {
        if ((err as Error).name !== "AbortError") setMissing(true);
      });

    return () => {
      abort.abort();
      cancelAnimationFrame(raf);
      removeEventListener("resize", onResize);
      removeEventListener(SETTLED, onSettled);
      removeEventListener(ELEVATION, onElevation);
      goRef.current = () => {};
      gsap.killTweensOf([camera.position, controls.target]);
      dispose(track);
      dispose(diorama);
      ground?.dispose();
      controls.dispose();
      composer.dispose();
      renderer.dispose();
    };
  }, [circuitId]);

  return (
    <>
      <canvas ref={canvasRef} className="fixed inset-0 block h-full w-full" aria-label="3D model of the circuit and its surroundings" />
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 bg-[radial-gradient(ellipse_70%_80%_at_34%_52%,transparent_50%,rgb(0_0_0/0.55)_100%)]"
      />
      {missing ? null : (
        <>
          <svg ref={leaderRef} aria-hidden="true" className="pointer-events-none fixed inset-0 hidden h-full w-full lg:block">
            <line ref={lineRef} stroke="rgb(255 255 255 / 0.35)" strokeWidth="1" />
            <circle ref={dotRef} r="4" fill="var(--color-signal)" />
          </svg>
          <div ref={anchorRef} className="pane fixed top-0 left-0 hidden w-[228px] rounded-3xl px-4 py-3.5 will-change-transform lg:block">
            {children}
          </div>
        </>
      )}
    </>
  );
}
