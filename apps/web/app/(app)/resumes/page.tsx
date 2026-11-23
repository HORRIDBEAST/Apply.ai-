"use client";

import { useRef, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import type { ResumeMeta } from "@/lib/types";

export default function ResumesPage() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const { data: resumes, isLoading } = useQuery({
    queryKey: ["resumes"],
    queryFn: () => api.resumes.list(getToken),
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => api.resumes.upload(getToken, file),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["resumes"] }),
  });

  const setPrimaryMutation = useMutation({
    mutationFn: (resumeId: string) => api.resumes.setPrimary(getToken, resumeId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["resumes"] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (resumeId: string) => api.resumes.remove(getToken, resumeId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["resumes"] }),
  });

  const { data: detail } = useQuery({
    queryKey: ["resume", selectedId],
    queryFn: () => api.resumes.get(getToken, selectedId as string),
    enabled: !!selectedId,
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Resumes</h1>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.doc,.docx"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) uploadMutation.mutate(file);
              e.target.value = "";
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploadMutation.isPending}
            className="rounded-full bg-neutral-900 px-5 py-2 text-sm font-medium text-white hover:bg-neutral-700 disabled:opacity-50"
          >
            {uploadMutation.isPending ? "Uploading…" : "Upload resume"}
          </button>
        </div>
      </div>

      {uploadMutation.isError && (
        <p className="text-sm text-red-600">Upload failed. Try a PDF or DOCX under 10MB.</p>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="rounded-2xl border border-neutral-200 bg-white">
          <div className="border-b border-neutral-200 px-6 py-4 font-semibold">
            Your resumes
          </div>
          {isLoading && <div className="px-6 py-6 text-sm text-neutral-500">Loading…</div>}
          {!isLoading && resumes?.length === 0 && (
            <div className="px-6 py-6 text-sm text-neutral-500">
              No resumes uploaded yet.
            </div>
          )}
          <ul className="divide-y divide-neutral-100">
            {resumes?.map((r: ResumeMeta) => (
              <li
                key={r.id}
                className={`flex cursor-pointer items-center justify-between px-6 py-4 hover:bg-neutral-50 ${selectedId === r.id ? "bg-neutral-50" : ""}`}
                onClick={() => setSelectedId(r.id)}
              >
                <div>
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{r.file_name}</span>
                    {r.is_primary && (
                      <span className="rounded-full bg-neutral-900 px-2 py-0.5 text-xs text-white">
                        Primary
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 text-xs text-neutral-500">
                    {r.parse_status} · {new Date(r.created_at).toLocaleDateString()}
                  </div>
                </div>
                <div className="flex gap-2">
                  {!r.is_primary && (
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setPrimaryMutation.mutate(r.id);
                      }}
                      className="text-xs text-neutral-500 hover:text-neutral-900"
                    >
                      Set primary
                    </button>
                  )}
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      deleteMutation.mutate(r.id);
                      if (selectedId === r.id) setSelectedId(null);
                    }}
                    className="text-xs text-red-500 hover:text-red-700"
                  >
                    Delete
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>

        <div className="rounded-2xl border border-neutral-200 bg-white">
          <div className="border-b border-neutral-200 px-6 py-4 font-semibold">
            Parsed data
          </div>
          {!selectedId && (
            <div className="px-6 py-6 text-sm text-neutral-500">
              Select a resume to view what the AI extracted from it.
            </div>
          )}
          {selectedId && !detail?.parsed_data && (
            <div className="px-6 py-6 text-sm text-neutral-500">
              Still parsing — check back in a moment.
            </div>
          )}
          {detail?.parsed_data && (
            <div className="space-y-4 px-6 py-5 text-sm">
              <div>
                <div className="font-medium">{detail.parsed_data.name}</div>
                <div className="text-neutral-500">{detail.parsed_data.email}</div>
                <div className="text-neutral-500">{detail.parsed_data.location}</div>
              </div>
              {detail.parsed_data.skills?.length > 0 && (
                <div>
                  <div className="mb-1 font-medium text-neutral-700">Skills</div>
                  <div className="flex flex-wrap gap-1.5">
                    {detail.parsed_data.skills.map((s) => (
                      <span key={s} className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs">
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {detail.parsed_data.experience?.length > 0 && (
                <div>
                  <div className="mb-1 font-medium text-neutral-700">Experience</div>
                  <ul className="space-y-2">
                    {detail.parsed_data.experience.map((exp, i) => (
                      <li key={i}>
                        <div className="font-medium">
                          {exp.title} · {exp.company}
                        </div>
                        <div className="text-xs text-neutral-500">
                          {exp.start_date} – {exp.end_date ?? "Present"}
                        </div>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
