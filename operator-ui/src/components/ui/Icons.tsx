// The console's own 16px stroke icons. Every status colour is paired with one of these and
// a text label, so state never depends on colour alone.

import type { SVGProps } from "react";

export type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function Base({ size = 16, children, ...props }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export const IconOverview = (p: IconProps) => (
  <Base {...p}>
    <path d="M2.25 11.25a5.75 5.75 0 1 1 11.5 0" />
    <path d="M8 11.25 10.6 8" />
    <path d="M4.2 7.3 3.4 6.8M8 5.5V4.6M11.8 7.3l.8-.5" />
  </Base>
);

export const IconApprovals = (p: IconProps) => (
  <Base {...p}>
    <path d="M8 1.75 13 3.65v3.6c0 3.1-2.1 5.6-5 6.9-2.9-1.3-5-3.8-5-6.9v-3.6L8 1.75Z" />
    <path d="m5.75 7.9 1.6 1.6 3-3.15" />
  </Base>
);

export const IconSessions = (p: IconProps) => (
  <Base {...p}>
    <rect x="4" y="4" width="8" height="8" rx="1.75" />
    <path d="M6.5 1.75V4M9.5 1.75V4M6.5 12v2.25M9.5 12v2.25M1.75 6.5H4M1.75 9.5H4M12 6.5h2.25M12 9.5h2.25" />
  </Base>
);

export const IconActivity = (p: IconProps) => (
  <Base {...p}>
    <path d="M1.5 8.25h2.6l1.8-4.75 3.1 9 1.85-4.25h3.65" />
  </Base>
);

export const IconPolicy = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.75 1.75h5.5l3 3v9.5h-8.5Z" />
    <path d="M9.25 1.75v3h3M6 8h4M6 10.75h4M6 5.5h1.5" />
  </Base>
);

export const IconSearch = (p: IconProps) => (
  <Base {...p}>
    <circle cx="7" cy="7" r="4.25" />
    <path d="m10.25 10.25 3.25 3.25" />
  </Base>
);

export const IconSun = (p: IconProps) => (
  <Base {...p}>
    <circle cx="8" cy="8" r="2.75" />
    <path d="M8 1.5v1.25M8 13.25v1.25M1.5 8h1.25M13.25 8h1.25M3.4 3.4l.9.9M11.7 11.7l.9.9M3.4 12.6l.9-.9M11.7 4.3l.9-.9" />
  </Base>
);

export const IconMoon = (p: IconProps) => (
  <Base {...p}>
    <path d="M13.25 9.6A5.5 5.5 0 0 1 6.4 2.75a5.5 5.5 0 1 0 6.85 6.85Z" />
  </Base>
);

export const IconMonitor = (p: IconProps) => (
  <Base {...p}>
    <rect x="1.75" y="2.5" width="12.5" height="8.5" rx="1.5" />
    <path d="M5.5 13.5h5M8 11v2.5" />
  </Base>
);

export const IconSignOut = (p: IconProps) => (
  <Base {...p}>
    <path d="M6 2.5H3.75c-.69 0-1.25.56-1.25 1.25v8.5c0 .69.56 1.25 1.25 1.25H6" />
    <path d="m10.5 11 3-3-3-3M13.5 8h-7" />
  </Base>
);

export const IconChevronRight = (p: IconProps) => (
  <Base {...p}>
    <path d="M6 3.5 10.5 8 6 12.5" />
  </Base>
);

export const IconChevronDown = (p: IconProps) => (
  <Base {...p}>
    <path d="M3.5 6 8 10.5 12.5 6" />
  </Base>
);

export const IconArrowRight = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 8h10M9 4l4 4-4 4" />
  </Base>
);

export const IconCopy = (p: IconProps) => (
  <Base {...p}>
    <rect x="5.25" y="5.25" width="8.5" height="8.5" rx="1.5" />
    <path d="M10.75 5.25v-1.5a1.5 1.5 0 0 0-1.5-1.5h-5a1.5 1.5 0 0 0-1.5 1.5v5a1.5 1.5 0 0 0 1.5 1.5h1.5" />
  </Base>
);

export const IconCheck = (p: IconProps) => (
  <Base {...p}>
    <path d="m3 8.5 3.2 3L13 4.5" />
  </Base>
);

export const IconCross = (p: IconProps) => (
  <Base {...p}>
    <path d="m4 4 8 8M12 4l-8 8" />
  </Base>
);

export const IconClock = (p: IconProps) => (
  <Base {...p}>
    <circle cx="8" cy="8" r="6" />
    <path d="M8 4.5V8l2.5 1.5" />
  </Base>
);

export const IconAlert = (p: IconProps) => (
  <Base {...p}>
    <path d="M8 2.2 14.2 13H1.8L8 2.2Z" />
    <path d="M8 6.5v3M8 11.4v.1" />
  </Base>
);

export const IconStop = (p: IconProps) => (
  <Base {...p}>
    <circle cx="8" cy="8" r="6" />
    <path d="M3.8 12.2 12.2 3.8" />
  </Base>
);

export const IconPlay = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 3.5v9l7-4.5-7-4.5Z" />
  </Base>
);

export const IconPause = (p: IconProps) => (
  <Base {...p}>
    <path d="M5.5 3.5v9M10.5 3.5v9" />
  </Base>
);

export const IconLoop = (p: IconProps) => (
  <Base {...p}>
    <path d="M12.5 6A5 5 0 0 0 3.6 5M3.5 10a5 5 0 0 0 8.9 1" />
    <path d="M3.5 2.5V5H6M12.5 13.5V11H10" />
  </Base>
);

export const IconHold = (p: IconProps) => (
  <Base {...p}>
    <path d="M8 1.75 14.25 8 8 14.25 1.75 8 8 1.75Z" />
    <path d="M6.75 6.25v3.5M9.25 6.25v3.5" />
  </Base>
);

export const IconPass = (p: IconProps) => (
  <Base {...p}>
    <path d="M1.75 8h12.5M10.75 4.5 14.25 8l-3.5 3.5" />
    <path d="M6 3v10" strokeOpacity={0.45} />
  </Base>
);

export const IconModel = (p: IconProps) => (
  <Base {...p}>
    <path d="M2.5 4.25c0-.97.78-1.75 1.75-1.75h7.5c.97 0 1.75.78 1.75 1.75v5c0 .97-.78 1.75-1.75 1.75H7.25L4.5 13.25V11h-.25c-.97 0-1.75-.78-1.75-1.75v-5Z" />
    <path d="M5.5 6.75h.01M8 6.75h.01M10.5 6.75h.01" />
  </Base>
);

export const IconServer = (p: IconProps) => (
  <Base {...p}>
    <rect x="2" y="2.5" width="12" height="4.5" rx="1.25" />
    <rect x="2" y="9" width="12" height="4.5" rx="1.25" />
    <path d="M4.75 4.75h.01M4.75 11.25h.01" />
  </Base>
);

export const IconCoins = (p: IconProps) => (
  <Base {...p}>
    <ellipse cx="8" cy="4.25" rx="4.75" ry="2" />
    <path d="M3.25 4.25v3.5c0 1.1 2.13 2 4.75 2s4.75-.9 4.75-2v-3.5M3.25 7.75v3.5c0 1.1 2.13 2 4.75 2s4.75-.9 4.75-2v-3.5" />
  </Base>
);

export const IconBell = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 10.75V7a4 4 0 1 1 8 0v3.75l1.25 1.5H2.75L4 10.75Z" />
    <path d="M6.5 14h3" />
  </Base>
);

export const IconTenant = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 14V3.5C3 2.67 3.67 2 4.5 2h5c.83 0 1.5.67 1.5 1.5V14M11 7h1.5c.83 0 1.5.67 1.5 1.5V14M1.5 14h13M5.5 5h3M5.5 8h3M5.5 11h3" />
  </Base>
);

export const IconMenu = (p: IconProps) => (
  <Base {...p}>
    <path d="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" />
  </Base>
);

export const IconKey = (p: IconProps) => (
  <Base {...p}>
    <circle cx="5.25" cy="10.75" r="2.75" />
    <path d="m7.25 8.75 6-6M11.25 4.75l1.5 1.5M9.75 6.25l1.25 1.25" />
  </Base>
);

export const IconExternal = (p: IconProps) => (
  <Base {...p}>
    <path d="M9.5 2.5h4v4M13.5 2.5 7.5 8.5" />
    <path d="M11.5 9.5v3.25c0 .41-.34.75-.75.75h-7.5a.75.75 0 0 1-.75-.75v-7.5c0-.41.34-.75.75-.75H6.5" />
  </Base>
);

export const IconReturn = (p: IconProps) => (
  <Base {...p}>
    <path d="M13 3.5v4.25c0 .69-.56 1.25-1.25 1.25H3.5M6.5 6 3.5 9l3 3" />
  </Base>
);
