import "@fontsource-variable/mona-sans/wdth.css";
import "@fontsource-variable/martian-mono/wdth.css";
import "./globals.css";

import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { THEME_STORAGE_KEY } from "@/lib/theme";

export const metadata: Metadata = {
  title: { default: "Operator console · MCP Gateway", template: "%s · MCP Gateway" },
  description: "Held tool calls, live agent traffic and spend, and circuit breakers for the MCP Gateway.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f5f6f8" },
    { media: "(prefers-color-scheme: dark)", color: "#090a0d" },
  ],
};

// Applies the operator's saved theme before first paint, so a dark-mode reviewer never
// sees a light flash. Runs inline (the CSP allows the console's own inline scripts).
const themeScript = `try{var t=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-screen font-sans antialiased">{children}</body>
    </html>
  );
}
