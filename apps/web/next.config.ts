import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  // Every page here fetches client-side (Clerk session + React Query
  // against the FastAPI backend), so there's no static/dynamic boundary
  // worth optimizing — Cache Components' strict prerendering checks just
  // add friction (e.g. reject <Show> in the landing page). Off for now.
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
