// Small stroke icons: every status colour in the console is paired with one of these and
// a text label, so state never depends on colour alone.

import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Base({ children, ...props }: IconProps) {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
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

export function ClockIcon(props: IconProps) {
  return (
    <Base {...props}>
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.5V8l2.5 1.5" />
    </Base>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M3 8.5l3.2 3L13 4.5" />
    </Base>
  );
}

export function CrossIcon(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M4 4l8 8M12 4l-8 8" />
    </Base>
  );
}

export function AlertIcon(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M8 2.2 14.2 13H1.8L8 2.2Z" />
      <path d="M8 6.5v3M8 11.4v.1" />
    </Base>
  );
}

export function StopIcon(props: IconProps) {
  return (
    <Base {...props}>
      <circle cx="8" cy="8" r="6" />
      <path d="M3.8 12.2 12.2 3.8" />
    </Base>
  );
}

export function PlayIcon(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M5 3.5v9l7-4.5-7-4.5Z" />
    </Base>
  );
}

export function LoopIcon(props: IconProps) {
  return (
    <Base {...props}>
      <path d="M12.5 6A5 5 0 0 0 3.6 5M3.5 10a5 5 0 0 0 8.9 1" />
      <path d="M3.5 2.5V5H6M12.5 13.5V11H10" />
    </Base>
  );
}
