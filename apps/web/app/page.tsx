import Link from "next/link";
import { Show } from "@clerk/nextjs";

export default function LandingPage() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center px-6 text-center">
      <div className="max-w-2xl">
        <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
          Apply to jobs faster — without the hallucinated cover letter.
        </h1>
        <p className="mt-4 text-lg text-neutral-600">
          Job Autofill Copilot fills out application forms using your real
          resume and your own pre-written answers. Every generated answer is
          grounded in what you actually wrote — nothing invented.
        </p>
        <div className="mt-8 flex items-center justify-center gap-4">
          <Show when="signed-out">
            <Link
              href="/sign-up"
              className="rounded-full bg-neutral-900 px-6 py-3 text-sm font-medium text-white hover:bg-neutral-700"
            >
              Get started
            </Link>
            <Link
              href="/sign-in"
              className="rounded-full border border-neutral-300 px-6 py-3 text-sm font-medium hover:bg-neutral-100"
            >
              Sign in
            </Link>
          </Show>
          <Show when="signed-in">
            <Link
              href="/dashboard"
              className="rounded-full bg-neutral-900 px-6 py-3 text-sm font-medium text-white hover:bg-neutral-700"
            >
              Go to dashboard
            </Link>
          </Show>
        </div>
      </div>
    </main>
  );
}
