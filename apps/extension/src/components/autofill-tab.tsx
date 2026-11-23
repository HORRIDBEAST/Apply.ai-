// src/components/autofill-tab.tsx
// The real workspace. Flow: scan the active tab's DOM via the content
// script -> POST /agents/form/understand -> POST
// /agents/answers/generate-batch for AI-required fields (template values
// are used directly for the rest) -> fill the page via the content script.

import { useAuth } from "@clerk/chrome-extension";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type BatchQuestion } from "~lib/api-client";
import { getSettings, setSelectedResume, setSelectedTemplate } from "~lib/storage";
import type { ContentMessage, ContentResponse } from "~contents/scanner";
import type { DetectedField, Template } from "~lib/types";

async function sendToActiveTab<T extends ContentResponse>(
  message: ContentMessage
): Promise<T> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("No active tab");
  return chrome.tabs.sendMessage(tab.id, message) as Promise<T>;
}

export function AutofillTab() {
  const { getToken } = useAuth();
  const [resumeId, setResumeId] = useState<string | null>(null);
  const [templateId, setTemplateId] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [filledCount, setFilledCount] = useState<{ filled: number; total: number } | null>(null);

  useEffect(() => {
    getSettings().then((s) => {
      setResumeId(s.selectedResumeId);
      setTemplateId(s.selectedTemplateId);
    });
  }, []);

  const { data: resumes } = useQuery({
    queryKey: ["resumes"],
    queryFn: async () => api.resumes.list((await getToken()) ?? "")
  });

  const { data: templates } = useQuery({
    queryKey: ["templates"],
    queryFn: async () => api.templates.list((await getToken()) ?? "")
  });

  const defaultTemplate = templates?.find((t) => t.is_default) ?? templates?.[0];
  const activeTemplateId = templateId ?? defaultTemplate?.id ?? null;
  const activeTemplate: Template | undefined = templates?.find((t) => t.id === activeTemplateId);

  const autofillMutation = useMutation({
    mutationFn: async () => {
      const token = (await getToken()) ?? "";
      if (!token) throw new Error("Not signed in");

      setStatus("Scanning page…");
      const scan = await sendToActiveTab<Extract<ContentResponse, { type: "SCAN_RESULT" }>>({
        type: "SCAN_PAGE"
      });

      setStatus("Detecting fields…");
      const understanding = await api.agents.understandForm(token, scan.html, scan.pageUrl);

      const templateAnswers = (activeTemplate?.answers_json ?? {}) as Record<string, unknown>;

      // Fields the regex heuristic already mapped to a template key get
      // filled directly from the template — only genuinely open-text
      // fields need an LLM call.
      const directFills: Array<{ field: DetectedField; value: string }> = [];
      const aiFields: Array<{ field: DetectedField; question: BatchQuestion }> = [];

      for (const field of understanding.fields) {
        const templateValue = templateAnswers[field.mapped_field];
        if (!field.requires_ai && typeof templateValue === "string" && templateValue) {
          directFills.push({ field, value: templateValue });
        } else if (field.requires_ai) {
          aiFields.push({
            field,
            question: { question: field.label, field_key: field.mapped_field || null }
          });
        }
      }

      if (aiFields.length > 0) {
        setStatus(`Generating ${aiFields.length} AI answer(s)…`);
        const batch = await api.agents.generateBatch(token, {
          questions: aiFields.map((f) => f.question),
          template_id: activeTemplateId,
          resume_id: resumeId
        });
        batch.answers.forEach((item, i) => {
          if (item.result.answer && !item.result.refusal_reason) {
            directFills.push({ field: aiFields[i].field, value: item.result.answer });
          }
        });
      }

      setStatus("Filling page…");
      const fillResult = await sendToActiveTab<Extract<ContentResponse, { type: "FILL_RESULT" }>>({
        type: "FILL_FIELDS",
        fields: directFills
      });

      const filled = fillResult.results.filter((r) => r.filled).length;
      setFilledCount({ filled, total: understanding.total_fields });
      setStatus("Done");
    },
    onError: (err) => setStatus(`Failed: ${String(err)}`)
  });

  return (
    <div style={{ padding: 16 }}>
      <button
        className="btn btn-primary"
        style={{ width: "100%", padding: "12px 0", fontSize: 14 }}
        onClick={() => autofillMutation.mutate()}
        disabled={autofillMutation.isPending}
      >
        {autofillMutation.isPending ? "Working…" : "⚡ Autofill this page"}
      </button>

      {status && (
        <p className="text-muted" style={{ fontSize: 12, marginTop: 8 }}>
          {status}
        </p>
      )}
      {filledCount && (
        <span className="badge badge-success" style={{ marginTop: 4, display: "inline-block" }}>
          Filled {filledCount.filled}/{filledCount.total}
        </span>
      )}

      <div className="card" style={{ marginTop: 16, padding: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Resume</div>
        <select
          value={resumeId ?? ""}
          onChange={(e) => {
            const v = e.target.value || null;
            setResumeId(v);
            setSelectedResume(v);
          }}
          style={{ width: "100%", padding: 6, fontSize: 12 }}
        >
          <option value="">Use primary resume</option>
          {resumes?.map((r) => (
            <option key={r.id} value={r.id}>
              {r.file_name}
            </option>
          ))}
        </select>
      </div>

      <div className="card" style={{ marginTop: 10, padding: 12 }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>Template</div>
        <select
          value={activeTemplateId ?? ""}
          onChange={(e) => {
            const v = e.target.value || null;
            setTemplateId(v);
            setSelectedTemplate(v);
          }}
          style={{ width: "100%", padding: 6, fontSize: 12 }}
        >
          {templates?.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name} {t.is_default ? "(default)" : ""}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
