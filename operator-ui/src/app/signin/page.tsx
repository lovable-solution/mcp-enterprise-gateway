import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SignInForm } from "@/components/signin/SignInForm";
import { SignInHero } from "@/components/signin/SignInHero";
import { LogoMark, Wordmark } from "@/components/ui/Logo";
import { config } from "@/lib/config";
import { currentSession } from "@/lib/server";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Sign in" };

const FACTS = [
  ["Four eyes", "A requester can never approve its own high-risk call."],
  ["Hard caps", "Calls, tokens and dollars are capped per session."],
  ["Loop breaker", "The fourth identical call in a minute pauses the session."],
] as const;

export default async function SignIn() {
  if ((await currentSession()) !== null) {
    redirect("/");
  }
  const { devSignIn, tenant } = config();
  return (
    <main className="grid min-h-screen lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
      <section className="relative hidden flex-col justify-between overflow-hidden border-r border-line bg-rail px-12 py-10 lg:flex">
        <div aria-hidden="true" className="dot-grid absolute inset-0 [mask-image:radial-gradient(ellipse_at_40%_45%,black_20%,transparent_70%)]" />
        <div className="relative flex items-center gap-3">
          <LogoMark size={30} live />
          <Wordmark className="text-[16px]" />
        </div>

        <div className="relative mx-auto w-full max-w-[600px]">
          <SignInHero />
          <h2 className="display mt-6 text-[34px] leading-[1.08] text-ink xl:text-[40px]">
            Every agent action
            <br />
            passes the gate.
          </h2>
          <p className="mt-4 max-w-md text-[14.5px] leading-relaxed text-ink-2">
            Review the calls that need a human, watch live spend against each session&apos;s caps, and stop loops before
            they cost anything.
          </p>
        </div>

        <ul className="relative grid grid-cols-3 gap-6 border-t border-line pt-6">
          {FACTS.map(([title, text]) => (
            <li key={title}>
              <p className="eyebrow text-ink-2">{title}</p>
              <p className="mt-2 text-[12.5px] leading-relaxed text-muted">{text}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-[400px]">
          <div className="mb-10 flex items-center gap-3 lg:hidden">
            <LogoMark size={28} live />
            <Wordmark />
          </div>
          <p className="eyebrow">Operator console</p>
          <h1 className="display mt-3 text-[30px] leading-tight text-ink">Sign in</h1>
          <p className="mt-2 text-[14px] text-ink-2">
            Tenant <span className="mono rounded-md bg-panel-3 px-1.5 py-0.5 text-[12px] text-ink">{tenant}</span>
          </p>
          <SignInForm devSignIn={devSignIn} />
        </div>
      </section>
    </main>
  );
}
