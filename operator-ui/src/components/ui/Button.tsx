import type { ButtonHTMLAttributes } from "react";

import { cx } from "@/lib/cx";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const VARIANT: Record<Variant, string> = {
  primary:
    "bg-accent text-on-accent hover:bg-accent-hover shadow-[inset_0_1px_0_rgb(255_255_255/0.18),0_1px_2px_rgb(0_0_0/0.2)]",
  secondary: "bg-panel-2 text-ink shadow-[0_0_0_1px_var(--line-strong)] hover:bg-panel-3",
  ghost: "text-ink-2 hover:bg-panel-3 hover:text-ink",
  danger: "bg-panel-2 text-critical-ink shadow-[0_0_0_1px_var(--line-strong)] hover:bg-critical/10",
};

const SIZE: Record<Size, string> = {
  sm: "h-7 gap-1.5 rounded-md px-2.5 text-[12.5px]",
  md: "h-9 gap-2 rounded-lg px-3.5 text-[13.5px]",
  lg: "h-11 gap-2 rounded-[10px] px-5 text-[14px]",
};

export function buttonClass(variant: Variant = "secondary", size: Size = "md", className?: string): string {
  return cx(
    "inline-flex shrink-0 select-none items-center justify-center font-medium whitespace-nowrap",
    "transition-[background-color,color,box-shadow,transform] duration-150 active:scale-[0.98]",
    "disabled:pointer-events-none disabled:opacity-50",
    VARIANT[variant],
    SIZE[size],
    className,
  );
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return <button type={type} className={buttonClass(variant, size, className)} {...props} />;
}
