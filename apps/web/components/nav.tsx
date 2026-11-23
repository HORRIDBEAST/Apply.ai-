"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { UserButton } from "@clerk/nextjs";

const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/resumes", label: "Resumes" },
  { href: "/templates", label: "Templates" },
  { href: "/memory", label: "Memory" },
];

export function Nav() {
  const pathname = usePathname();

  return (
    <header className="flex h-16 items-center justify-between border-b border-neutral-200 bg-white px-6">
      <div className="flex items-center gap-8">
        <Link href="/dashboard" className="text-sm font-semibold tracking-tight">
          Job Autofill Copilot
        </Link>
        <nav className="flex items-center gap-1">
          {LINKS.map((link) => {
            const active = pathname === link.href;
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  active
                    ? "bg-neutral-900 text-white"
                    : "text-neutral-600 hover:bg-neutral-100 hover:text-neutral-900"
                }`}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>
      </div>
      <UserButton />
    </header>
  );
}
