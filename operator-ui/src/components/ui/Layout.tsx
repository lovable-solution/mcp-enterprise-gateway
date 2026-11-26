import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: ReactNode;
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0">
        {eyebrow && <div className="eyebrow flex items-center gap-2">{eyebrow}</div>}
        <h1 className="display mt-2.5 text-[28px] leading-[1.05] sm:text-[30px]">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-[13.5px] leading-relaxed text-ink-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function Panel({
  title,
  meta,
  actions,
  children,
  className,
  bodyClassName,
  labelledBy,
}: {
  title?: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  labelledBy?: string;
}) {
  return (
    <section className={cx("panel flex min-w-0 flex-col", className)} aria-labelledby={labelledBy}>
      {(title || actions) && (
        <header className="flex min-h-12 items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2.5">
            {title && (
              <h2 id={labelledBy} className="truncate text-[13.5px] font-semibold text-ink">
                {title}
              </h2>
            )}
            {meta}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx("min-h-0 flex-1", bodyClassName)}>{children}</div>
    </section>
  );
}

/** The figure in a panel header: "3" beside "Needs a decision". */
export function Count({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "warning" | "critical" }) {
  return (
    <span
      className={cx(
        "mono inline-flex h-5 min-w-5 items-center justify-center rounded-md px-1.5 text-[10.5px] font-medium",
        tone === "muted" && "bg-panel-3 text-ink-2",
        tone === "warning" && "bg-warning/15 text-warning-ink",
        tone === "critical" && "bg-critical/10 text-critical-ink",
      )}
    >
      {children}
    </span>
  );
}
