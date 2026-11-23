// src/popup.tsx
// Toolbar-icon launcher — Simplify-style quick menu, scoped to what our
// backend actually supports (see frontend_context.md §1a/§3 for the
// Simplify reference and the scope decision). Each entry either opens the
// side panel (the real workspace) or deep-links to the website.

import { Show, SignInButton, useUser } from "@clerk/chrome-extension";
import { Providers } from "~components/providers";
import "~style.css";

const WEBSITE_URL = process.env.PLASMO_PUBLIC_CLERK_SYNC_HOST ?? "http://localhost:3000";

function openSidePanel() {
  chrome.windows.getCurrent((win) => {
    if (win.id !== undefined) {
      chrome.sidePanel.open({ windowId: win.id });
    }
  });
  window.close();
}

function MenuItem({
  label,
  description,
  onClick
}: {
  label: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="card"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        width: "100%",
        padding: "12px 14px",
        marginBottom: 8,
        textAlign: "left",
        border: "1px solid #e5e5e5"
      }}
    >
      <span style={{ fontWeight: 600, fontSize: 13 }}>{label}</span>
      <span className="text-muted" style={{ fontSize: 12, marginTop: 2 }}>
        {description}
      </span>
    </button>
  );
}

function PopupContent() {
  const { user } = useUser();

  return (
    <div style={{ width: 320, padding: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
        <div
          style={{
            width: 28,
            height: 28,
            borderRadius: "50%",
            background: "#171717",
            color: "white",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 13,
            fontWeight: 600
          }}
        >
          C
        </div>
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>Job Autofill Copilot</div>
          {user && <div className="text-muted" style={{ fontSize: 11 }}>{user.primaryEmailAddress?.emailAddress}</div>}
        </div>
      </div>

      <Show when="signed-out">
        <p className="text-muted" style={{ fontSize: 13, marginBottom: 12 }}>
          Sign in to start autofilling applications.
        </p>
        <SignInButton>
          <button className="btn btn-primary" style={{ width: "100%" }}>
            Sign in
          </button>
        </SignInButton>
      </Show>

      <Show when="signed-in">
        <MenuItem
          label="Autofill this page"
          description="Detect the form and fill it from your resume + templates"
          onClick={openSidePanel}
        />
        <MenuItem
          label="View profile"
          description="See what the AI extracted from your resume"
          onClick={openSidePanel}
        />
        <MenuItem
          label="Templates"
          description="Manage your pre-written answers"
          onClick={() => window.open(`${WEBSITE_URL}/templates`, "_blank")}
        />
        <MenuItem
          label="Application history"
          description="View everything you've applied to"
          onClick={() => window.open(`${WEBSITE_URL}/dashboard`, "_blank")}
        />
      </Show>
    </div>
  );
}

export default function Popup() {
  return (
    <Providers>
      <PopupContent />
    </Providers>
  );
}
