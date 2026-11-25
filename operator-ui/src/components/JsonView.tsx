"use client";

import { useState } from "react";

// The exact payload a reviewer approves, rendered as a collapsible tree. Values are
// rendered as text nodes (React escapes them): tool arguments are untrusted input.

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

function Scalar({ value }: { value: Exclude<Json, Json[] | { [key: string]: Json }> }) {
  if (value === null) {
    return <span className="text-muted">null</span>;
  }
  if (typeof value === "string") {
    return <span className="break-words text-ink">&quot;{value}&quot;</span>;
  }
  if (typeof value === "number") {
    return <span className="figures font-semibold text-ink">{value.toLocaleString("en-US")}</span>;
  }
  return <span className="font-semibold text-ink">{String(value)}</span>;
}

function Node({ name, value, depth }: { name: string | null; value: Json; depth: number }) {
  const [open, setOpen] = useState(depth < 3);
  const key = name === null ? null : <span className="text-ink-2">{name}: </span>;
  if (value === null || typeof value !== "object") {
    return (
      <li className="py-0.5">
        {key}
        <Scalar value={value} />
      </li>
    );
  }
  const entries: [string, Json][] = Array.isArray(value)
    ? value.map((item, index) => [String(index), item])
    : Object.entries(value);
  const brackets = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  return (
    <li className="py-0.5">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="rounded text-left hover:bg-hairline"
      >
        <span className="inline-block w-3 text-muted">{open ? "▾" : "▸"}</span>
        {key}
        <span className="text-muted">
          {brackets[0]}
          {!open && ` ${entries.length} ${entries.length === 1 ? "item" : "items"} ${brackets[1]}`}
        </span>
      </button>
      {open && (
        <>
          <ul className="ml-4 border-l border-grid pl-3">
            {entries.map(([childName, child]) => (
              <Node key={childName} name={Array.isArray(value) ? null : childName} value={child} depth={depth + 1} />
            ))}
          </ul>
          <span className="ml-3 text-muted">{brackets[1]}</span>
        </>
      )}
    </li>
  );
}

export function JsonView({ value, label }: { value: unknown; label: string }) {
  const [raw, setRaw] = useState(false);
  const json = (value ?? null) as Json;
  return (
    <figure className="rounded-lg border border-hairline bg-page">
      <figcaption className="flex items-center justify-between border-b border-hairline px-3 py-1.5 text-xs text-ink-2">
        <span>{label}</span>
        <button type="button" onClick={() => setRaw(!raw)} className="rounded px-1.5 py-0.5 hover:bg-hairline">
          {raw ? "Tree" : "Raw JSON"}
        </button>
      </figcaption>
      {raw ? (
        <pre className="figures max-h-80 overflow-auto p-3 font-mono text-xs leading-relaxed">
          {JSON.stringify(json, null, 2)}
        </pre>
      ) : (
        <ul className="max-h-80 overflow-auto p-3 font-mono text-xs leading-relaxed">
          <Node name={null} value={json} depth={0} />
        </ul>
      )}
    </figure>
  );
}
