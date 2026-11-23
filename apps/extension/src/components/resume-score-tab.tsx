// src/components/resume-score-tab.tsx
// Placeholder — the backend endpoint (POST /api/v1/agents/resume/score)
// and the job-description capture pipeline it needs both exist
// (backend/api/agents/resume_scoring.py, backend/api/routers/job_descriptions.py)
// but nothing wires a captured JD into this tab yet. Ships visible rather
// than hidden so the 3-tab layout matches Simplify's now and just needs
// wiring later — see frontend_context.md §5 ("option B" groundwork).

export function ResumeScoreTab() {
  return (
    <div style={{ padding: 16, textAlign: "center" }}>
      <div style={{ fontSize: 32, marginTop: 24 }}>🚧</div>
      <h3 style={{ fontSize: 14, fontWeight: 600, marginTop: 8 }}>Coming soon</h3>
      <p className="text-muted" style={{ fontSize: 12, marginTop: 6, lineHeight: 1.5 }}>
        Resume-to-job matching is on the way. The scoring engine already
        exists on the backend — this tab will light up once it's wired to
        a captured job description.
      </p>
    </div>
  );
}
