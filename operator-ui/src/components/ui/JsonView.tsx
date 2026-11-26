"use client";

import { useState } from "react";

import { cx } from "@/lib/cx";

import { CopyButton } from "./Misc";

// The exact payload a reviewer approves, as a collapsible tree. Values render as text
// nodes (React escapes them): tool arguments are untrusted input.

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

function Scalar({ value }: { value: Exclude<Json, Json[] | { [key: string]: Json }> }) {
  if (value === null) {
    return <span className="text-muted">null</span>;
  }
  if (typeof value === "string") {
    return <span className="break-words text-good-ink">&quot;{value}&quot;</span>;
  }
  if (typeof value === "number") {
    return <span className="figures text-accent">{value.toLocaleString("en-US")}</span>;
  }
  return <span className="text-serious-ink">{String(value)}</span>;
}

function Node({ name, value, depth }: { name: string | null; value: Json; depth: number }) {
  const [open, setOpen] = useState(depth < 3);
  const key = name === null ? null : <span className="text-ink-2">{name}: </span>;
  if (value === null || typeof value !== "object") {
    return (
      <li className="py-[3px]">
        {key}
        <Scalar value={value} />
      </li>
    );
  }
  const entries: [string, Json][] = Array.isArray(value)
    ? value.map((item, index) => [String(index), item])
    : Object.entries(value);
  const [opening, closing] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  return (
    <li className="py-[3px]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="rounded text-left hover:bg-panel-3"
      >
        <span className={cx("inline-block w-3 text-muted transition-transform", open && "rotate-90")}>›</span>
        {key}
        <span className="text-muted">
          {opening}
          {!open && ` ${entries.length} ${entries.length === 1 ? "item" : "items"} ${closing}`}
        </span>
      </button>
      {open && (
        <>
          <ul className="ml-[5px] border-l border-line-strong pl-3.5">
            {entries.map(([childName, child]) => (
              <Node key={childName} name={Array.isArray(value) ? null : childName} value={child} depth={depth + 1} />
            ))}
          </ul>
          <span className="ml-3 text-muted">{closing}</span>
        </>
      )}
    </li>
  );
}

export function JsonView({ value, label, footer }: { value: unknown; label: string; footer?: string }) {
  const [raw, setRaw] = useState(false);
  const json = (value ?? null) as Json;
  const text = JSON.stringify(json, null, 2);
  return (
    <figure className="overflow-hidden rounded-[10px] bg-panel-2 shadow-[0_0_0_1px_var(--line)]">
      <figcaption className="flex items-center justify-between gap-2 border-b border-line px-3 py-1.5">
        <span className="eyebrow">{label}</span>
        <span className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setRaw(!raw)}
            className="rounded-md px-2 py-1 text-[11.5px] font-medium text-ink-2 hover:bg-panel-3 hover:text-ink"
          >
            {raw ? "Tree" : "Raw"}
          </button>
          <CopyButton value={text} label="Copy JSON" />
        </span>
      </figcaption>
      {raw ? (
        <pre className="mono figures scrollbar-thin max-h-80 overflow-auto p-3.5 text-[11.5px] leading-relaxed">{text}</pre>
      ) : (
        <ul className="mono scrollbar-thin max-h-80 overflow-auto p-3.5 text-[11.5px] leading-relaxed">
          <Node name={null} value={json} depth={0} />
        </ul>
      )}
      {footer && <p className="mono truncate border-t border-line px-3 py-1.5 text-[10px] text-muted">{footer}</p>}
    </figure>
  );
}
