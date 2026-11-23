// src/sidepanel.tsx
// The real workspace, injected as Chrome's native side panel while on a
// job application page. 3 tabs mirroring Simplify's layout
// (frontend_context.md §1b): Autofill (live), Resume Score (stub),
// Profile (live).

import { Show, SignInButton, useUser } from "@clerk/chrome-extension";
import { useState } from "react";
import { Providers } from "~components/providers";
import { AutofillTab } from "~components/autofill-tab";
import { ResumeScoreTab } from "~components/resume-score-tab";
import { ProfileTab } from "~components/profile-tab";
import "~style.css";

type TabKey = "autofill" | "score" | "profile";

function SidePanelContent() {
  const [tab, setTab] = useState<TabKey>("autofill");
  const { user } = useUser();

  return (
    <div style={{ minHeight: "100vh" }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 16px",
          borderBottom: "1px solid #e5e5e5"
        }}
      >
        <span style={{ fontWeight: 600, fontSize: 13 }}>Job Autofill Copilot</span>
        {user && (
          <span className="text-muted" style={{ fontSize: 11 }}>
            {user.firstName}
          </span>
        )}
      </div>

      <Show when="signed-out">
        <div style={{ padding: 16 }}>
          <p className="text-muted" style={{ fontSize: 13, marginBottom: 12 }}>
            Sign in to use Autofill.
          </p>
          <SignInButton>
            <button className="btn btn-primary" style={{ width: "100%" }}>
              Sign in
            </button>
          </SignInButton>
        </div>
      </Show>

      <Show when="signed-in">
        <div className="tabs">
          <button
            className={`tab ${tab === "autofill" ? "active" : ""}`}
            onClick={() => setTab("autofill")}
          >
            Autofill
          </button>
          <button
            className={`tab ${tab === "score" ? "active" : ""}`}
            onClick={() => setTab("score")}
          >
            Resume Score
          </button>
          <button
            className={`tab ${tab === "profile" ? "active" : ""}`}
            onClick={() => setTab("profile")}
          >
            Profile
          </button>
        </div>

        {tab === "autofill" && <AutofillTab />}
        {tab === "score" && <ResumeScoreTab />}
        {tab === "profile" && <ProfileTab />}
      </Show>
    </div>
  );
}

export default function SidePanel() {
  return (
    <Providers>
      <SidePanelContent />
    </Providers>
  );
}
