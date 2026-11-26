"use client";

import { useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";

// The sign-in illustration: agent lanes feeding a gate, a few calls passing to tool
// servers, one parked for review, one turned away. Decorative only (aria-hidden); the
// console's live view draws real traffic. SMIL animation scales with the viewBox; it is
// left out under reduced motion, which keeps the static picture.

const LANES = [70, 130, 190, 250];
const GATE_X = 330;
const SERVERS = [112, 214];

function route(lane: number, server: number): string {
  const y0 = LANES[lane] ?? 70;
  const y1 = SERVERS[server] ?? 112;
  return `M48 ${y0} L${GATE_X} ${y0} C ${GATE_X + 80} ${y0}, ${GATE_X + 90} ${y1}, 486 ${y1}`;
}

const PASSES: readonly { lane: number; server: number; begin: number; dur: number }[] = [
  { lane: 0, server: 0, begin: 0, dur: 3.4 },
  { lane: 1, server: 1, begin: 1.2, dur: 3.8 },
  { lane: 3, server: 1, begin: 2.1, dur: 3.6 },
  { lane: 0, server: 1, begin: 2.9, dur: 3.9 },
  { lane: 1, server: 0, begin: 4.2, dur: 3.5 },
];

export function SignInHero() {
  const reduce = useReducedMotion();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const animate = mounted && !reduce;

  return (
    <svg viewBox="0 0 560 320" className="h-auto w-full" aria-hidden="true" focusable="false">
      <defs>
        <radialGradient id="hero-fade" cx="50%" cy="50%" r="60%">
          <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.07" />
          <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
        </radialGradient>
        <filter id="hero-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feMerge>
            <feMergeNode in="blur" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
      </defs>
      <ellipse cx={GATE_X} cy="160" rx="250" ry="150" fill="url(#hero-fade)" />

      {LANES.map((y) =>
        SERVERS.map((s) => (
          <path
            key={`${y}-${s}`}
            d={`M${GATE_X + 8} ${y} C ${GATE_X + 80} ${y}, ${GATE_X + 90} ${s}, 486 ${s}`}
            fill="none"
            stroke="var(--line-strong)"
            strokeWidth="1"
          />
        )),
      )}
      {LANES.map((y) => (
        <g key={y}>
          <line x1="56" x2={GATE_X - 10} y1={y} y2={y} stroke="var(--line-strong)" strokeDasharray="2 5" />
          <circle cx="48" cy={y} r="6" fill="var(--panel)" stroke="var(--accent)" strokeWidth="1.75" />
          <circle cx="48" cy={y} r="2.4" fill="var(--accent)" />
        </g>
      ))}

      <rect x={GATE_X - 7} y="36" width="14" height="250" rx="7" fill="var(--panel-2)" stroke="var(--line-strong)" />
      {Array.from({ length: 26 }, (_, index) => 46 + index * 9.4).map((y) => (
        <g key={y} stroke="var(--line-strong)">
          <line x1={GATE_X - 12} x2={GATE_X - 9} y1={y} y2={y} />
          <line x1={GATE_X + 9} x2={GATE_X + 12} y1={y} y2={y} />
        </g>
      ))}
      <text x={GATE_X} y="24" textAnchor="middle" className="mono" fontSize="9" letterSpacing="1.4" fill="var(--muted)">
        GATEWAY
      </text>

      {SERVERS.map((y) => (
        <g key={y}>
          <rect x="486" y={y - 11} width="22" height="22" rx="6" fill="var(--panel)" stroke="var(--line-strong)" strokeWidth="1.5" />
          <circle cx="497" cy={y} r="3" fill="var(--good)" />
        </g>
      ))}

      {/* A held call, parked at the gate. */}
      <g transform={`translate(${GATE_X - 22} ${LANES[2]}) rotate(45)`} filter="url(#hero-glow)">
        <rect x="-6" y="-6" width="12" height="12" rx="2" fill="var(--warning)">
          {animate && <animate attributeName="opacity" values="1;0.55;1" dur="2.4s" repeatCount="indefinite" />}
        </rect>
      </g>

      {animate &&
        PASSES.map((pass, index) => (
          <circle key={index} r="3.6" fill="var(--accent)" opacity="0" filter="url(#hero-glow)">
            <animateMotion
              dur={`${pass.dur}s`}
              begin={`${pass.begin}s`}
              repeatCount="indefinite"
              path={route(pass.lane, pass.server)}
              calcMode="spline"
              keyPoints="0;1"
              keyTimes="0;1"
              keySplines="0.45 0 0.35 1"
            />
            <animate
              attributeName="opacity"
              values="0;1;1;0"
              keyTimes="0;0.08;0.9;1"
              dur={`${pass.dur}s`}
              begin={`${pass.begin}s`}
              repeatCount="indefinite"
            />
          </circle>
        ))}

      {animate && (
        <circle r="3.6" fill="var(--critical)" opacity="0" filter="url(#hero-glow)">
          <animateMotion
            dur="4.6s"
            begin="1.6s"
            repeatCount="indefinite"
            path={`M48 ${LANES[3]} L${GATE_X - 10} ${LANES[3]}`}
            keyPoints="0;1;0.82;0.82"
            keyTimes="0;0.55;0.7;1"
            calcMode="linear"
          />
          <animate
            attributeName="opacity"
            values="0;1;1;0;0"
            keyTimes="0;0.08;0.55;0.72;1"
            dur="4.6s"
            begin="1.6s"
            repeatCount="indefinite"
          />
        </circle>
      )}
    </svg>
  );
}
