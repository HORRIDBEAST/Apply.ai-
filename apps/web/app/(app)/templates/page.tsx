"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import type { Template, TemplateAnswers } from "@/lib/types";

const EMPTY_ANSWERS: TemplateAnswers = {
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  linkedin_url: "",
  github_url: "",
  portfolio_url: "",
  current_location: "",
  cover_letter: "",
  why_us: "",
  about_yourself: "",
  strengths: "",
};

export default function TemplatesPage() {
  const { getToken } = useAuth();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [form, setForm] = useState<TemplateAnswers>(EMPTY_ANSWERS);
  const [name, setName] = useState("New template");

  const { data: templates } = useQuery({
    queryKey: ["templates"],
    queryFn: () => api.templates.list(getToken),
  });

  const selected = templates?.find((t) => t.id === selectedId) ?? null;

  useEffect(() => {
    if (selected) {
      setForm({ ...EMPTY_ANSWERS, ...selected.answers_json });
      setName(selected.name);
    }
  }, [selected]);

  const createMutation = useMutation({
    mutationFn: () => api.templates.create(getToken, { name, answers_json: form }),
    onSuccess: (t: Template) => {
      queryClient.invalidateQueries({ queryKey: ["templates"] });
      setSelectedId(t.id);
    },
  });

  const updateMutation = useMutation({
    mutationFn: () => {
      if (!selectedId) throw new Error("No template selected");
      return api.templates.update(getToken, selectedId, { name, answers_json: form });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["templates"] }),
  });

  const setDefaultMutation = useMutation({
    mutationFn: (id: string) => api.templates.setDefault(getToken, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["templates"] }),
  });

  const field = (key: keyof TemplateAnswers, label: string, multiline = false) => (
    <div>
      <label className="mb-1 block text-xs font-medium text-neutral-600">{label}</label>
      {multiline ? (
        <textarea
          value={(form[key] as string) ?? ""}
          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
          rows={3}
          className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
      ) : (
        <input
          value={(form[key] as string) ?? ""}
          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
          className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
        />
      )}
    </div>
  );

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[280px_1fr]">
      <div className="rounded-2xl border border-neutral-200 bg-white">
        <div className="flex items-center justify-between border-b border-neutral-200 px-4 py-3">
          <span className="font-semibold">Templates</span>
          <button
            onClick={() => {
              setSelectedId(null);
              setForm(EMPTY_ANSWERS);
              setName("New template");
            }}
            className="text-xs text-neutral-500 hover:text-neutral-900"
          >
            + New
          </button>
        </div>
        <ul className="divide-y divide-neutral-100">
          {templates?.map((t) => (
            <li
              key={t.id}
              onClick={() => setSelectedId(t.id)}
              className={`cursor-pointer px-4 py-3 text-sm hover:bg-neutral-50 ${selectedId === t.id ? "bg-neutral-50 font-medium" : ""}`}
            >
              <div className="flex items-center gap-2">
                {t.name}
                {t.is_default && (
                  <span className="rounded-full bg-neutral-900 px-1.5 py-0.5 text-[10px] text-white">
                    default
                  </span>
                )}
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-2xl border border-neutral-200 bg-white p-6">
        <div className="mb-4 flex items-center justify-between">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="text-lg font-semibold outline-none"
          />
          <div className="flex gap-2">
            {selectedId && !selected?.is_default && (
              <button
                onClick={() => setDefaultMutation.mutate(selectedId)}
                className="rounded-full border border-neutral-300 px-4 py-1.5 text-xs font-medium hover:bg-neutral-100"
              >
                Make default
              </button>
            )}
            <button
              onClick={() => (selectedId ? updateMutation.mutate() : createMutation.mutate())}
              disabled={createMutation.isPending || updateMutation.isPending}
              className="rounded-full bg-neutral-900 px-4 py-1.5 text-xs font-medium text-white hover:bg-neutral-700 disabled:opacity-50"
            >
              {selectedId ? "Save changes" : "Create template"}
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {field("first_name", "First name")}
          {field("last_name", "Last name")}
          {field("email", "Email")}
          {field("phone", "Phone")}
          {field("linkedin_url", "LinkedIn URL")}
          {field("github_url", "GitHub URL")}
          {field("portfolio_url", "Portfolio URL")}
          {field("current_location", "Current location")}
        </div>
        <div className="mt-4 space-y-4">
          {field("cover_letter", "Cover letter", true)}
          {field("why_us", "Why us?", true)}
          {field("about_yourself", "About yourself", true)}
          {field("strengths", "Strengths", true)}
        </div>
      </div>
    </div>
  );
}
