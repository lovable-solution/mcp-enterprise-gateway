"use client";

import { AnimatePresence, motion } from "motion/react";
import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { cx } from "@/lib/cx";
import { formatToolName } from "@/lib/format";
import { argumentLine, shortId } from "@/lib/present";
import { applyTheme } from "@/lib/theme";

import { useConsole } from "../console/ConsoleProvider";
import { IconClock, IconMonitor, IconMoon, IconReturn, IconSearch, IconSessions, IconSignOut, IconSun } from "../ui/Icons";
import { Kbd } from "../ui/Misc";
import { NAV } from "./Sidebar";

interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: ReactNode;
  run: () => void;
}

export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  return <AnimatePresence>{open && <Dialog onClose={onClose} />}</AnimatePresence>;
}

function Dialog({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const { pending, sessions, signOut } = useConsole();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const list = useRef<HTMLUListElement>(null);

  const commands = useMemo<Command[]>(() => {
    const go = (href: string) => () => {
      onClose();
      router.push(href);
    };
    return [
      ...NAV.map((item) => ({
        id: `nav:${item.href}`,
        group: "Go to",
        label: item.label,
        icon: <item.icon size={15} />,
        run: go(item.href),
      })),
      ...pending.map((approval) => ({
        id: `approval:${approval.id}`,
        group: "Held calls",
        label: formatToolName(approval.tool_name).tool,
        hint: [argumentLine(approval.arguments, 2), approval.requester_subject].filter(Boolean).join(" · "),
        icon: <IconClock size={15} className="text-warning" />,
        run: go(`/approvals?id=${approval.id}`),
      })),
      ...sessions.slice(0, 8).map((status) => ({
        id: `session:${status.session.session_id}`,
        group: "Sessions",
        label: status.session.subject,
        hint: `${status.session.role} · ${shortId(status.session.session_id, 12)}`,
        icon: <IconSessions size={15} />,
        run: go(`/sessions?id=${encodeURIComponent(status.session.session_id)}`),
      })),
      { id: "theme:system", group: "Theme", label: "Use system theme", icon: <IconMonitor size={15} />, run: () => applyTheme("system") },
      { id: "theme:light", group: "Theme", label: "Use light theme", icon: <IconSun size={15} />, run: () => applyTheme("light") },
      { id: "theme:dark", group: "Theme", label: "Use dark theme", icon: <IconMoon size={15} />, run: () => applyTheme("dark") },
      {
        id: "signout",
        group: "Account",
        label: "Sign out",
        icon: <IconSignOut size={15} />,
        run: () => {
          onClose();
          void signOut();
        },
      },
    ];
  }, [pending, sessions, router, onClose, signOut]);

  const needle = query.trim().toLowerCase();
  const results = needle
    ? commands.filter((command) => `${command.label} ${command.hint ?? ""} ${command.group}`.toLowerCase().includes(needle))
    : commands;
  const selected = Math.min(active, Math.max(results.length - 1, 0));

  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-index="${selected}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((selected + 1) % Math.max(results.length, 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((selected - 1 + results.length) % Math.max(results.length, 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      results[selected]?.run();
    } else if (event.key === "Escape") {
      event.preventDefault();
      onClose();
    }
  }

  let previousGroup = "";
  return (
    <motion.div
      className="fixed inset-0 z-[70] flex items-start justify-center px-4 pt-[12vh]"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
    >
      <div className="absolute inset-0 bg-[var(--scrim)] backdrop-blur-[2px]" onClick={onClose} />
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-label="Search and commands"
        initial={{ opacity: 0, y: -12, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -8, scale: 0.98 }}
        transition={{ type: "spring", stiffness: 520, damping: 38 }}
        className="relative w-full max-w-[560px] overflow-hidden rounded-2xl bg-panel shadow-[var(--shadow-pop)]"
        onKeyDown={onKeyDown}
      >
        <div className="flex items-center gap-3 border-b border-line px-4">
          <IconSearch size={16} className="text-muted" />
          <input
            autoFocus
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            placeholder="Search held calls, sessions, pages…"
            className="h-13 flex-1 bg-transparent py-4 text-[14.5px] text-ink outline-none placeholder:text-muted"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-results"
            aria-activedescendant={results[selected] ? `palette-${results[selected].id}` : undefined}
          />
          <Kbd>esc</Kbd>
        </div>
        <ul id="palette-results" ref={list} role="listbox" className="scrollbar-thin max-h-[52vh] overflow-y-auto p-2">
          {results.length === 0 && <li className="px-3 py-8 text-center text-[13px] text-muted">Nothing matches “{query}”.</li>}
          {results.map((command, index) => {
            const heading = command.group !== previousGroup ? command.group : null;
            previousGroup = command.group;
            return (
              <li key={command.id} role="presentation">
                {heading && <p className="eyebrow px-3 pt-3 pb-1.5">{heading}</p>}
                <div
                  id={`palette-${command.id}`}
                  role="option"
                  aria-selected={index === selected}
                  data-index={index}
                  onMouseMove={() => setActive(index)}
                  onClick={() => command.run()}
                  className={cx(
                    "flex h-10 cursor-pointer items-center gap-3 rounded-lg px-3 text-[13.5px]",
                    index === selected ? "bg-panel-3 text-ink" : "text-ink-2",
                  )}
                >
                  <span className="inline-flex text-muted">{command.icon}</span>
                  <span className="font-medium text-ink">{command.label}</span>
                  {command.hint && <span className="truncate text-[12.5px] text-muted">{command.hint}</span>}
                  {index === selected && <IconReturn size={13} className="ml-auto shrink-0 text-muted" />}
                </div>
              </li>
            );
          })}
        </ul>
        <div className="flex items-center gap-4 border-t border-line px-4 py-2 text-[11.5px] text-muted">
          <span className="flex items-center gap-1.5">
            <Kbd>↑</Kbd>
            <Kbd>↓</Kbd> move
          </span>
          <span className="flex items-center gap-1.5">
            <Kbd>↵</Kbd> open
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            <Kbd>⌘K</Kbd> toggle
          </span>
        </div>
      </motion.div>
    </motion.div>
  );
}
