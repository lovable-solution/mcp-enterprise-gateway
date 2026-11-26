"use client";

import { useReducedMotion } from "motion/react";
import { useRouter } from "next/navigation";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { formatToolName, formatUsd } from "@/lib/format";
import { formatCountdown } from "@/lib/present";
import { classify, serverOf, toolOf } from "@/lib/traffic";
import type { Approval, GatewayEvent, PolicySummary, SessionStatus } from "@/lib/types";

import { isFresh } from "../console/ConsoleProvider";

// The gate: every real tool call travels from its agent session (left) through the gateway
// (centre) to a tool server (right). Allowed calls pass, denied calls bounce off, held calls
// park at the gate until someone decides them. Driven only by live gateway events; nothing
// here is simulated. Under reduced motion it draws the same picture without movement.

const MAX_LANES = 5;
const TRAVEL_IN = 560;
const TRAVEL_OUT = 620;
const ACTIVE_MS = 60_000;

type ParticleKind = "allowed" | "denied" | "held" | "executed" | "dropped" | "model" | "breaker";

interface Particle {
  kind: ParticleKind;
  lane: number;
  server: number;
  born: number;
}

interface Lane {
  key: string;
  sessionIds: string[];
  label: string;
  sub: string;
  active: boolean;
  exhausted: boolean;
  breaker: boolean;
}

interface Server {
  name: string;
  tools: number;
  health: string;
}

interface Geometry {
  width: number;
  height: number;
  agentX: number;
  gateX: number;
  serverX: number;
  laneY: (index: number) => number;
  serverY: (index: number) => number;
}

function geometry(width: number, height: number, lanes: number, servers: number): Geometry {
  const top = 58;
  const bottom = 34;
  const usable = Math.max(40, height - top - bottom);
  const leftLabel = Math.min(210, Math.max(104, width * 0.19));
  const rightLabel = Math.min(190, Math.max(96, width * 0.17));
  const agentX = leftLabel + 18;
  const serverX = width - rightLabel - 18;
  return {
    width,
    height,
    agentX,
    serverX,
    gateX: agentX + (serverX - agentX) * 0.52,
    laneY: (index) => top + (usable * (index + 0.5)) / Math.max(lanes, 1),
    serverY: (index) => top + (usable * (index + 0.5)) / Math.max(servers, 1),
  };
}

function bezier(t: number, p0: number, p1: number, p2: number, p3: number): number {
  const u = 1 - t;
  return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3;
}

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);
const easeOut = (t: number) => 1 - (1 - t) ** 3;

interface Palette {
  accent: string;
  warning: string;
  critical: string;
  good: string;
  ink: string;
  ink2: string;
  muted: string;
  line: string;
  lineStrong: string;
  panel: string;
  panel2: string;
  dark: boolean;
}

function readPalette(element: HTMLElement): Palette {
  const style = getComputedStyle(element);
  const get = (name: string) => style.getPropertyValue(name).trim() || "#888";
  return {
    accent: get("--accent"),
    warning: get("--warning"),
    critical: get("--critical"),
    good: get("--good"),
    ink: get("--ink"),
    ink2: get("--ink-2"),
    muted: get("--muted"),
    line: get("--line"),
    lineStrong: get("--line-strong"),
    panel: get("--panel"),
    panel2: get("--panel-2"),
    dark: style.colorScheme.includes("dark"),
  };
}

export function buildLanes(sessions: SessionStatus[], now: number, stateOf: (s: SessionStatus) => string): Lane[] {
  const recent = sessions.slice(0, MAX_LANES);
  const rest = sessions.slice(MAX_LANES);
  const stable = [...recent].sort((a, b) => Date.parse(a.session.first_seen) - Date.parse(b.session.first_seen));
  const subjects = new Map<string, number>();
  for (const status of stable) {
    subjects.set(status.session.subject, (subjects.get(status.session.subject) ?? 0) + 1);
  }
  const lanes: Lane[] = stable.map((status) => ({
    key: status.session.session_id,
    sessionIds: [status.session.session_id],
    label: status.session.subject,
    // One agent often runs several sessions: the id's tail tells their lanes apart.
    sub: [
      status.session.role,
      (subjects.get(status.session.subject) ?? 0) > 1 ? `…${status.session.session_id.slice(-4)}` : null,
      formatUsd(status.budget.cost_usd),
    ]
      .filter(Boolean)
      .join(" · "),
    active: now - Date.parse(status.session.last_seen) < ACTIVE_MS,
    exhausted: stateOf(status) === "exhausted",
    breaker: status.circuit_breaker.open,
  }));
  if (rest.length > 0) {
    lanes.push({
      key: "others",
      sessionIds: rest.map((status) => status.session.session_id),
      label: `${rest.length} more ${rest.length === 1 ? "session" : "sessions"}`,
      sub: "older activity",
      active: rest.some((status) => now - Date.parse(status.session.last_seen) < ACTIVE_MS),
      exhausted: false,
      breaker: rest.some((status) => status.circuit_breaker.open),
    });
  }
  return lanes;
}

export function buildServers(policy: PolicySummary | null, events: readonly GatewayEvent[]): Server[] {
  const servers = new Map<string, Server>();
  for (const [name, health] of Object.entries(policy?.upstreams ?? {})) {
    servers.set(name, { name, tools: 0, health });
  }
  for (const tool of policy?.tools ?? []) {
    const name = serverOf(tool.name);
    if (name) {
      const server = servers.get(name) ?? { name, tools: 0, health: "ok" };
      server.tools += 1;
      servers.set(name, server);
    }
  }
  for (const event of events) {
    const name = serverOf(toolOf(event));
    if (name && !servers.has(name)) {
      servers.set(name, { name, tools: 0, health: "ok" });
    }
  }
  return [...servers.values()].slice(0, 5);
}

export function TrafficGate({
  sessions,
  events,
  pending,
  policy,
  now,
  stateOf,
}: {
  sessions: SessionStatus[];
  events: readonly GatewayEvent[];
  pending: Approval[];
  policy: PolicySummary | null;
  now: number;
  stateOf: (status: SessionStatus) => string;
}) {
  const router = useRouter();
  const reduce = useReducedMotion() ?? false;
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [hover, setHover] = useState<{ approval: Approval; x: number; y: number } | null>(null);

  const lanes = useMemo(() => buildLanes(sessions, now, stateOf), [sessions, now, stateOf]);
  const servers = useMemo(() => buildServers(policy, events), [policy, events]);
  const geo = useMemo(
    () => geometry(size.width, size.height, Math.max(lanes.length, 1), Math.max(servers.length, 1)),
    [size, lanes.length, servers.length],
  );

  // Everything the animation loop reads, kept current without restarting the loop.
  const scene = useRef({ lanes, servers, geo, pending, reduce });
  useLayoutEffect(() => {
    scene.current = { lanes, servers, geo, pending, reduce };
  }, [lanes, servers, geo, pending, reduce]);
  const particles = useRef<Particle[]>([]);
  /** When each held call's particle reaches the gate; its diamond appears then. */
  const arrivals = useRef(new Map<string, number>());
  const lastTraffic = useRef(0);

  // Turn each new event into a particle; events already on screen at mount do not replay.
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    const first = seen.current === null;
    const known = (seen.current ??= new Set());
    const fresh: GatewayEvent[] = [];
    for (const event of events) {
      const key = event.id ?? `${event.type}:${event.occurred_at}:${event.session_id ?? ""}`;
      if (!known.has(key)) {
        known.add(key);
        if (!first) {
          fresh.push(event);
        }
      }
    }
    if (fresh.length === 0 || scene.current.reduce) {
      return;
    }
    const { lanes: currentLanes, servers: currentServers } = scene.current;
    const born = performance.now();
    fresh.reverse().forEach((event, order) => {
      if (!isFresh(event)) {
        return; // history loaded at connect is already on the board, not in flight
      }
      const sessionId = event.session_id;
      const lane = sessionId === null ? -1 : currentLanes.findIndex((entry) => entry.sessionIds.includes(sessionId));
      if (lane < 0) {
        return;
      }
      const serverName = serverOf(toolOf(event));
      const server = serverName === null ? -1 : currentServers.findIndex((entry) => entry.name === serverName);
      const kind = classify(event);
      const at = born + order * 90;
      const spawn = (particle: ParticleKind) => particles.current.push({ kind: particle, lane, server, born: at });
      if (kind === "allowed" || kind === "denied" || kind === "executed" || kind === "model" || kind === "breaker") {
        spawn(kind);
      } else if (kind === "held") {
        spawn("held");
        if (typeof event.data.approval_id === "string") {
          arrivals.current.set(event.data.approval_id, at + TRAVEL_IN);
        }
      } else if (kind === "rejected" || kind === "expired") {
        spawn("dropped");
      }
      if (kind !== "model") {
        lastTraffic.current = at;
      }
    });
    if (particles.current.length > 160) {
      particles.current = particles.current.slice(-160);
    }
  }, [events]);

  useLayoutEffect(() => {
    const element = box.current;
    if (!element) {
      return;
    }
    const measure = () => {
      const rect = element.getBoundingClientRect();
      setSize({ width: Math.round(rect.width), height: Math.round(rect.height) });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const element = canvas.current;
    const context = element?.getContext("2d");
    if (!element || !context) {
      return;
    }
    let palette = readPalette(element);
    const refreshPalette = () => {
      palette = readPalette(element);
    };
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    scheme.addEventListener("change", refreshPalette);
    window.addEventListener("console-theme", refreshPalette);

    let frame = 0;
    const draw = (time: number) => {
      frame = requestAnimationFrame(draw);
      const { lanes: L, servers: S, geo: G, pending: P, reduce: still } = scene.current;
      const dpr = window.devicePixelRatio || 1;
      if (element.width !== Math.round(G.width * dpr) || element.height !== Math.round(G.height * dpr)) {
        element.width = Math.round(G.width * dpr);
        element.height = Math.round(G.height * dpr);
      }
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
      context.clearRect(0, 0, G.width, G.height);
      if (G.width < 10) {
        return;
      }
      const c = context;
      const laneCount = Math.max(L.length, 1);
      const gateTop = 40;
      const gateBottom = G.height - 22;
      const gateHalf = 6;

      // Fan of routes from the gate to each server.
      c.lineWidth = 1;
      for (let lane = 0; lane < laneCount; lane += 1) {
        const y0 = G.laneY(lane);
        for (let server = 0; server < S.length; server += 1) {
          const y1 = G.serverY(server);
          const dx = G.serverX - G.gateX;
          c.strokeStyle = palette.line;
          c.beginPath();
          c.moveTo(G.gateX + gateHalf, y0);
          c.bezierCurveTo(G.gateX + dx * 0.5, y0, G.serverX - dx * 0.45, y1, G.serverX - 8, y1);
          c.stroke();
        }
      }

      // Lanes from each agent to the gate.
      L.forEach((lane, index) => {
        const y = G.laneY(index);
        c.setLineDash([2, 5]);
        c.strokeStyle = lane.breaker ? palette.critical : palette.lineStrong;
        c.globalAlpha = lane.breaker ? 0.55 : 1;
        c.beginPath();
        c.moveTo(G.agentX + 8, y);
        c.lineTo(G.gateX - gateHalf - 2, y);
        c.stroke();
        c.setLineDash([]);
        c.globalAlpha = 1;
      });

      // The gate itself: a rail with instrument ticks.
      c.fillStyle = palette.panel2;
      c.strokeStyle = palette.lineStrong;
      c.beginPath();
      c.roundRect(G.gateX - gateHalf, gateTop, gateHalf * 2, gateBottom - gateTop, gateHalf);
      c.fill();
      c.stroke();
      c.strokeStyle = palette.lineStrong;
      for (let y = gateTop + 8; y < gateBottom - 4; y += 9) {
        c.beginPath();
        c.moveTo(G.gateX - gateHalf - 4, y);
        c.lineTo(G.gateX - gateHalf - 1, y);
        c.moveTo(G.gateX + gateHalf + 1, y);
        c.lineTo(G.gateX + gateHalf + 4, y);
        c.stroke();
      }
      // Idle scan: a slow glint down the rail when nothing has passed for a while.
      if (!still && time - lastTraffic.current > 20_000) {
        const span = gateBottom - gateTop;
        const y = gateTop + ((time / 3600) % 1) * span;
        const glint = c.createLinearGradient(0, y - 26, 0, y + 26);
        glint.addColorStop(0, "transparent");
        glint.addColorStop(0.5, palette.accent);
        glint.addColorStop(1, "transparent");
        c.globalAlpha = 0.35;
        c.fillStyle = glint;
        c.fillRect(G.gateX - gateHalf + 2, Math.max(gateTop, y - 26), gateHalf * 2 - 4, 52);
        c.globalAlpha = 1;
      }

      // Agent nodes.
      L.forEach((lane, index) => {
        const y = G.laneY(index);
        const colour = lane.exhausted ? palette.critical : lane.active ? palette.accent : palette.muted;
        if (lane.active && !still) {
          c.globalAlpha = 0.16 + 0.08 * Math.sin(time / 520 + index);
          c.fillStyle = colour;
          c.beginPath();
          c.arc(G.agentX, y, 10, 0, Math.PI * 2);
          c.fill();
          c.globalAlpha = 1;
        }
        c.fillStyle = palette.panel;
        c.strokeStyle = colour;
        c.lineWidth = 1.75;
        c.beginPath();
        c.arc(G.agentX, y, 5, 0, Math.PI * 2);
        c.fill();
        c.stroke();
        c.fillStyle = colour;
        c.beginPath();
        c.arc(G.agentX, y, 2.2, 0, Math.PI * 2);
        c.fill();
        c.lineWidth = 1;
      });

      // Server nodes.
      S.forEach((server, index) => {
        const y = G.serverY(index);
        const healthy = server.health === "ok";
        c.fillStyle = palette.panel;
        c.strokeStyle = healthy ? palette.lineStrong : palette.critical;
        c.lineWidth = 1.5;
        c.beginPath();
        c.roundRect(G.serverX - 8, y - 8, 16, 16, 4);
        c.fill();
        c.stroke();
        c.fillStyle = healthy ? palette.good : server.health === "pending" ? palette.muted : palette.critical;
        c.beginPath();
        c.arc(G.serverX, y, 2.4, 0, Math.PI * 2);
        c.fill();
        c.lineWidth = 1;
      });

      // Circuit breakers: a bar across the lane at the gate.
      L.forEach((lane, index) => {
        if (!lane.breaker) {
          return;
        }
        const y = G.laneY(index);
        c.fillStyle = palette.critical;
        c.beginPath();
        c.roundRect(G.gateX - gateHalf - 3, y - 11, gateHalf * 2 + 6, 22, 3);
        c.fill();
        c.strokeStyle = palette.panel;
        c.lineWidth = 1.5;
        c.beginPath();
        c.moveTo(G.gateX - 3.5, y - 3.5);
        c.lineTo(G.gateX + 3.5, y + 3.5);
        c.moveTo(G.gateX + 3.5, y - 3.5);
        c.lineTo(G.gateX - 3.5, y + 3.5);
        c.stroke();
        c.lineWidth = 1;
      });

      // Held calls park in front of the gate until they are decided.
      const stacks = new Map<number, number>();
      for (const approval of P) {
        const lane = L.findIndex((entry) => entry.sessionIds.includes(approval.session_id));
        const arrival = arrivals.current.get(approval.id);
        if (lane < 0 || (arrival !== undefined && time < arrival)) {
          continue;
        }
        const slot = stacks.get(lane) ?? 0;
        stacks.set(lane, slot + 1);
        const x = G.gateX - gateHalf - 13 - slot * 14;
        const y = G.laneY(lane);
        const pulse = still ? 1 : 1 + 0.09 * Math.sin(time / 380 + slot);
        drawDiamond(c, x, y, 5.5 * pulse, palette.warning, palette.dark);
      }

      // Particles.
      const alive: Particle[] = [];
      for (const particle of particles.current) {
        const age = time - particle.born;
        if (age < 0 || drawParticle(c, particle, age, G, palette, gateHalf)) {
          alive.push(particle);
        }
      }
      particles.current = alive;
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      scheme.removeEventListener("change", refreshPalette);
      window.removeEventListener("console-theme", refreshPalette);
    };
  }, []);

  // Pointer: held diamonds open their approval.
  function heldAt(x: number, y: number): { approval: Approval; x: number; y: number } | null {
    const stacks = new Map<number, number>();
    for (const approval of pending) {
      const lane = lanes.findIndex((entry) => entry.sessionIds.includes(approval.session_id));
      if (lane < 0) {
        continue;
      }
      const slot = stacks.get(lane) ?? 0;
      stacks.set(lane, slot + 1);
      const cx = geo.gateX - 6 - 13 - slot * 14;
      const cy = geo.laneY(lane);
      if (Math.abs(x - cx) <= 9 && Math.abs(y - cy) <= 9) {
        return { approval, x: cx, y: cy };
      }
    }
    return null;
  }

  const quiet = lanes.length === 0;
  return (
    <div ref={box} className="relative h-[300px] w-full sm:h-[330px]">
      <div
        aria-hidden="true"
        className="dot-grid absolute inset-0 [mask-image:radial-gradient(ellipse_at_center,black_35%,transparent_80%)]"
      />
      <canvas
        ref={canvas}
        className="absolute inset-0 h-full w-full"
        style={{ cursor: hover ? "pointer" : "default" }}
        role="img"
        aria-label={`Live traffic: ${lanes.length} agent ${lanes.length === 1 ? "session" : "sessions"}, ${pending.length} held ${pending.length === 1 ? "call" : "calls"} at the gate.`}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const next = heldAt(event.clientX - rect.left, event.clientY - rect.top);
          setHover((previous) => (previous?.approval.id === next?.approval.id ? previous : next));
        }}
        onMouseLeave={() => setHover(null)}
        onClick={() => hover && router.push(`/approvals?id=${hover.approval.id}`)}
      />

      {size.width > 0 && (
        <div className="pointer-events-none absolute inset-0">
          <span className="eyebrow absolute top-4" style={{ left: 16 }}>
            Agent sessions
          </span>
          <span className="eyebrow absolute top-4 -translate-x-1/2" style={{ left: geo.gateX }}>
            Gateway
          </span>
          <span className="eyebrow absolute top-4" style={{ left: geo.serverX - 8 }}>
            Tool servers
          </span>

          {lanes.map((lane, index) => (
            <div
              key={lane.key}
              className="absolute -translate-y-1/2 pr-3 text-right"
              style={{ top: geo.laneY(index), left: 12, width: geo.agentX - 12 - 6 }}
            >
              <p className={`truncate text-[12.5px] font-medium ${lane.active ? "text-ink" : "text-ink-2"}`}>
                {lane.label}
              </p>
              <p className="mono truncate text-[9.5px] text-muted">{lane.breaker ? "loop stopped" : lane.sub}</p>
            </div>
          ))}

          {servers.map((server, index) => (
            <div
              key={server.name}
              className="absolute -translate-y-1/2 pl-4"
              style={{ top: geo.serverY(index), left: geo.serverX + 4, width: geo.width - geo.serverX - 12 }}
            >
              <p className="truncate text-[12.5px] font-medium text-ink">{server.name}</p>
              <p className="mono truncate text-[9.5px] text-muted">
                {server.tools > 0 ? `${server.tools} ${server.tools === 1 ? "tool" : "tools"} · ` : ""}
                {server.health === "ok" ? "healthy" : server.health}
              </p>
            </div>
          ))}

          {policy && (
            <span
              className="mono absolute bottom-1.5 -translate-x-1/2 text-[9.5px] whitespace-nowrap text-muted"
              style={{ left: geo.gateX }}
            >
              {policy.name} · {policy.digest.replace("sha256:", "").slice(0, 8)}
            </span>
          )}

          {quiet && (
            <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 text-center">
              <p className="text-[13.5px] font-medium text-ink-2">No agent traffic yet</p>
              <p className="mt-1 text-[12.5px] text-muted">Calls appear here the moment an agent connects.</p>
            </div>
          )}

          {hover && (
            <div
              className="absolute z-10 w-56 -translate-x-1/2 -translate-y-[calc(100%+14px)] rounded-lg bg-panel px-3 py-2 shadow-[var(--shadow-pop)]"
              style={{ left: hover.x, top: hover.y }}
            >
              <p className="text-[12.5px] font-semibold text-ink">{formatToolName(hover.approval.tool_name).tool}</p>
              <p className="mt-0.5 text-[11.5px] text-ink-2">
                Held · expires in {formatCountdown(Date.parse(hover.approval.expires_at) - now)}
              </p>
              <p className="mt-1 text-[11px] font-medium text-accent">Click to review</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function drawDiamond(c: CanvasRenderingContext2D, x: number, y: number, r: number, colour: string, glow: boolean) {
  c.save();
  if (glow) {
    c.shadowColor = colour;
    c.shadowBlur = 12;
  }
  c.fillStyle = colour;
  c.beginPath();
  c.moveTo(x, y - r);
  c.lineTo(x + r, y);
  c.lineTo(x, y + r);
  c.lineTo(x - r, y);
  c.closePath();
  c.fill();
  c.restore();
}

function dot(c: CanvasRenderingContext2D, x: number, y: number, r: number, colour: string, glow: boolean, alpha = 1) {
  c.save();
  c.globalAlpha = alpha;
  if (glow) {
    c.shadowColor = colour;
    c.shadowBlur = 10;
  }
  c.fillStyle = colour;
  c.beginPath();
  c.arc(x, y, r, 0, Math.PI * 2);
  c.fill();
  c.restore();
}

function ring(c: CanvasRenderingContext2D, x: number, y: number, r: number, colour: string, alpha: number, width = 1.5) {
  c.save();
  c.globalAlpha = Math.max(0, alpha);
  c.strokeStyle = colour;
  c.lineWidth = width;
  c.beginPath();
  c.arc(x, y, r, 0, Math.PI * 2);
  c.stroke();
  c.restore();
}

function trail(c: CanvasRenderingContext2D, x0: number, y0: number, x1: number, y1: number, colour: string) {
  const gradient = c.createLinearGradient(x0, y0, x1, y1);
  gradient.addColorStop(0, "transparent");
  gradient.addColorStop(1, colour);
  c.save();
  c.strokeStyle = gradient;
  c.globalAlpha = 0.7;
  c.lineWidth = 2;
  c.lineCap = "round";
  c.beginPath();
  c.moveTo(x0, y0);
  c.lineTo(x1, y1);
  c.stroke();
  c.restore();
}

/** Draws one particle at `age` ms; returns false once it has finished. */
function drawParticle(
  c: CanvasRenderingContext2D,
  particle: Particle,
  age: number,
  G: Geometry,
  palette: Palette,
  gateHalf: number,
): boolean {
  const y = G.laneY(particle.lane);
  const startX = G.agentX + 6;
  const gateInX = G.gateX - gateHalf - 3;
  const glow = palette.dark;

  const inbound = (colour: string): boolean => {
    if (age > TRAVEL_IN) {
      return false;
    }
    const t = easeInOut(age / TRAVEL_IN);
    const x = startX + (gateInX - startX) * t;
    trail(c, Math.max(startX, x - 34), y, x, y, colour);
    dot(c, x, y, 3.2, colour, glow);
    return true;
  };

  const outbound = (colour: string, start: number): boolean => {
    const local = age - start;
    if (local < 0 || local > TRAVEL_OUT + 420) {
      return local < 0;
    }
    const serverY = particle.server >= 0 ? G.serverY(particle.server) : y;
    const endX = particle.server >= 0 ? G.serverX - 9 : G.gateX + (G.serverX - G.gateX) * 0.45;
    if (local <= TRAVEL_OUT) {
      const t = easeInOut(local / TRAVEL_OUT);
      const dx = G.serverX - G.gateX;
      const x0 = G.gateX + gateHalf;
      const px = bezier(t, x0, G.gateX + dx * 0.5, G.serverX - dx * 0.45, endX);
      const py = bezier(t, y, y, serverY, serverY);
      const back = Math.max(0, t - 0.06);
      const tx = bezier(back, x0, G.gateX + dx * 0.5, G.serverX - dx * 0.45, endX);
      const ty = bezier(back, y, y, serverY, serverY);
      trail(c, tx, ty, px, py, colour);
      dot(c, px, py, 3.2, colour, glow, particle.server >= 0 ? 1 : 1 - t);
    } else if (particle.server >= 0) {
      const t = (local - TRAVEL_OUT) / 420;
      ring(c, G.serverX, serverY, 9 + 10 * easeOut(t), colour, 0.7 * (1 - t));
    }
    return true;
  };

  const flash = (colour: string, start: number, spread = 12): void => {
    const local = age - start;
    if (local < 0 || local > 380) {
      return;
    }
    const t = local / 380;
    c.save();
    c.globalAlpha = 0.55 * (1 - t);
    c.fillStyle = colour;
    c.fillRect(G.gateX - gateHalf, y - spread * (1 + t), gateHalf * 2, spread * 2 * (1 + t));
    c.restore();
  };

  switch (particle.kind) {
    case "allowed": {
      const a = inbound(palette.accent);
      flash(palette.accent, TRAVEL_IN);
      const b = outbound(palette.accent, TRAVEL_IN + 60);
      return a || b;
    }
    case "executed": {
      flash(palette.good, 0);
      return outbound(palette.good, 80);
    }
    case "held": {
      const a = inbound(palette.warning);
      const local = age - TRAVEL_IN;
      if (local >= 0 && local < 700) {
        ring(c, G.gateX - gateHalf - 13, y, 6 + 14 * easeOut(local / 700), palette.warning, 0.8 * (1 - local / 700));
      }
      return a || local < 700;
    }
    case "denied": {
      const a = inbound(palette.critical);
      const local = age - TRAVEL_IN;
      if (local >= 0 && local <= 520) {
        const t = local / 520;
        flash(palette.critical, TRAVEL_IN, 14);
        ring(c, gateInX, y, 4 + 18 * easeOut(t), palette.critical, 0.9 * (1 - t), 2);
        const x = gateInX - 46 * easeOut(t);
        const drop = 10 * t * t;
        dot(c, x, y + drop, 3.2, palette.critical, glow, 1 - t);
      }
      return a || local <= 520;
    }
    case "dropped": {
      if (age > 600) {
        return false;
      }
      const t = age / 600;
      drawDiamond(c, G.gateX - gateHalf - 13, y + 18 * t * t, 5.5 * (1 - t * 0.4), palette.muted, false);
      return true;
    }
    case "model": {
      if (age > 900) {
        return false;
      }
      const t = age / 900;
      ring(c, G.agentX, y, 6 + 16 * easeOut(t), palette.ink2, 0.5 * (1 - t));
      return true;
    }
    case "breaker": {
      if (age > 800) {
        return false;
      }
      const t = age / 800;
      ring(c, G.gateX, y, 10 + 26 * easeOut(t), palette.critical, 0.9 * (1 - t), 2.5);
      flash(palette.critical, 0, 18);
      return true;
    }
  }
}
