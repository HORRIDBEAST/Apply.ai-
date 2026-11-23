"use client";

import { useEffect, useRef } from "react";
import { useAuth, useUser } from "@clerk/nextjs";
import { api } from "@/lib/api-client";

/**
 * No real Clerk webhook is configured yet (see project_clerk_auth_blocked
 * memory / frontend_context.md), so the backend's `users` row for a
 * freshly signed-up Clerk user doesn't exist until something calls
 * POST /users/sync. This mounts once in the authenticated layout and does
 * that using Clerk's own client-side user object as the stopgap source of
 * truth — safe to call repeatedly, it's an upsert.
 */
export function EnsureSynced() {
  const { isLoaded, isSignedIn } = useAuth();
  const { user } = useUser();
  const synced = useRef(false);

  useEffect(() => {
    if (!isLoaded || !isSignedIn || !user || synced.current) return;
    synced.current = true;

    api.users
      .sync({
        id: user.id,
        primaryEmailAddress: user.primaryEmailAddress?.emailAddress ?? "",
        firstName: user.firstName,
        lastName: user.lastName,
        imageUrl: user.imageUrl,
      })
      .catch((err) => {
        console.error("Failed to sync user with backend", err);
        synced.current = false; // allow retry on next mount
      });
  }, [isLoaded, isSignedIn, user]);

  return null;
}
