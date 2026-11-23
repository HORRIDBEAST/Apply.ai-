// src/components/providers.tsx
// Shared wrapper for both popup.tsx and sidepanel.tsx: Clerk (via the
// chrome-extension SDK, which syncs the session from the website tab —
// see PLASMO_PUBLIC_CLERK_SYNC_HOST in .env) + React Query.
//
// STATUS: wired per Clerk's documented chrome-extension pattern but not
// yet verified against a loaded extension — real end-to-end Clerk testing
// is deferred, same as the backend (see project_clerk_auth_blocked memory
// and frontend_context.md §5). The website side already uses the real
// Clerk SDK too, so once that's confirmed working, this should just work.

import { ClerkProvider } from "@clerk/chrome-extension";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

const PUBLISHABLE_KEY = process.env.PLASMO_PUBLIC_CLERK_PUBLISHABLE_KEY;
const SYNC_HOST = process.env.PLASMO_PUBLIC_CLERK_SYNC_HOST;

if (!PUBLISHABLE_KEY) {
  throw new Error(
    "PLASMO_PUBLIC_CLERK_PUBLISHABLE_KEY is missing — set it in apps/extension/.env"
  );
}

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} syncHost={SYNC_HOST}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ClerkProvider>
  );
}
