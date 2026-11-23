// src/components/profile-tab.tsx
// Read-only, click-to-copy reference view of the user's primary resume —
// mirrors Simplify's Profile tab layout (frontend_context.md §1b).

import { useAuth } from "@clerk/chrome-extension";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "~lib/api-client";

function CopyBlock({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  if (!value) return null;

  return (
    <div
      className="card"
      style={{ padding: "10px 12px", marginBottom: 8, cursor: "pointer" }}
      onClick={() => {
        navigator.clipboard.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span className="text-muted" style={{ fontSize: 11 }}>
          {label}
        </span>
        <span style={{ fontSize: 11, color: copied ? "#15803d" : "#a3a3a3" }}>
          {copied ? "Copied!" : "Click to copy"}
        </span>
      </div>
      <div style={{ fontSize: 13, marginTop: 2, whiteSpace: "pre-wrap" }}>{value}</div>
    </div>
  );
}

export function ProfileTab() {
  const { getToken } = useAuth();

  const { data: resumes } = useQuery({
    queryKey: ["resumes"],
    queryFn: async () => api.resumes.list((await getToken()) ?? "")
  });

  const primary = resumes?.find((r) => r.is_primary) ?? resumes?.[0];

  const { data: detail } = useQuery({
    queryKey: ["resume", primary?.id],
    queryFn: async () => api.resumes.get((await getToken()) ?? "", primary!.id),
    enabled: !!primary
  });

  const parsed = detail?.parsed_data;

  if (!primary) {
    return (
      <div style={{ padding: 16 }} className="text-muted">
        <p style={{ fontSize: 13 }}>No resume uploaded yet. Upload one from the website dashboard.</p>
      </div>
    );
  }

  if (!parsed) {
    return (
      <div style={{ padding: 16 }} className="text-muted">
        <p style={{ fontSize: 13 }}>Still parsing your resume — check back in a moment.</p>
      </div>
    );
  }

  return (
    <div style={{ padding: 16 }}>
      <p className="text-muted" style={{ fontSize: 11, marginBottom: 10 }}>
        Click any block to copy it — use these to fill fields the AI couldn't map.
      </p>

      <CopyBlock label="Name" value={parsed.name} />
      <CopyBlock label="Email" value={parsed.email} />
      <CopyBlock label="Phone" value={parsed.phone ?? ""} />
      <CopyBlock label="Location" value={parsed.location ?? ""} />
      <CopyBlock label="LinkedIn" value={parsed.linkedin_url ?? ""} />
      <CopyBlock label="GitHub" value={parsed.github_url ?? ""} />
      <CopyBlock label="Portfolio" value={parsed.portfolio_url ?? ""} />

      {parsed.skills.length > 0 && <CopyBlock label="Skills" value={parsed.skills.join(", ")} />}

      {parsed.experience.map((exp, i) => (
        <CopyBlock
          key={i}
          label={`${exp.title} · ${exp.company}`}
          value={exp.description}
        />
      ))}
    </div>
  );
}
