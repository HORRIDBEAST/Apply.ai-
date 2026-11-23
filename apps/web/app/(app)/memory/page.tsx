"use client";

import { useState } from "react";
import { useAuth } from "@clerk/nextjs";
import { useMutation } from "@tanstack/react-query";
import { api } from "@/lib/api-client";
import type { SimilarAnswer } from "@/lib/types";

export default function MemoryPage() {
  const { getToken } = useAuth();
  const [question, setQuestion] = useState("");
  const [results, setResults] = useState<SimilarAnswer[] | null>(null);

  const searchMutation = useMutation({
    mutationFn: (q: string) => api.memory.search(getToken, { question: q, top_k: 10 }),
    onSuccess: (data) => setResults(data.similar_answers),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-semibold">Memory</h1>
      <p className="text-sm text-neutral-500">
        Search past answers you&apos;ve submitted on other applications — the
        same semantic search the autofill agent uses to reuse your own words.
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (question.trim()) searchMutation.mutate(question);
        }}
        className="flex gap-2"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. Why do you want to work here?"
          className="flex-1 rounded-full border border-neutral-300 px-4 py-2 text-sm"
        />
        <button
          type="submit"
          disabled={searchMutation.isPending}
          className="rounded-full bg-neutral-900 px-5 py-2 text-sm font-medium text-white hover:bg-neutral-700 disabled:opacity-50"
        >
          {searchMutation.isPending ? "Searching…" : "Search"}
        </button>
      </form>

      {results && results.length === 0 && (
        <p className="text-sm text-neutral-500">No similar past answers found.</p>
      )}

      {results && results.length > 0 && (
        <ul className="space-y-3">
          {results.map((r, i) => (
            <li key={i} className="rounded-xl border border-neutral-200 bg-white p-4">
              <div className="flex items-center justify-between text-xs text-neutral-500">
                <span>{r.question_label}</span>
                <span>{Math.round(r.score * 100)}% match</span>
              </div>
              <p className="mt-2 text-sm">{r.answer_text}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
