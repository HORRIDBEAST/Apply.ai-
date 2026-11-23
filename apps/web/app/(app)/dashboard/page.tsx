"use client";

import { useAuth, useUser } from "@clerk/nextjs";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { api } from "@/lib/api-client";
import type { ApplicationStatus } from "@/lib/types";

const STATUS_STYLES: Record<ApplicationStatus, string> = {
  detected: "bg-neutral-100 text-neutral-700",
  in_progress: "bg-blue-100 text-blue-700",
  filled: "bg-amber-100 text-amber-700",
  submitted: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  abandoned: "bg-neutral-100 text-neutral-500",
};

export default function DashboardPage() {
  const { getToken } = useAuth();
  const { user } = useUser();

  const { data, isLoading } = useQuery({
    queryKey: ["applications", 1],
    queryFn: () => api.applications.list(getToken, { page: 1, page_size: 10 }),
  });

  const items = data?.items ?? [];
  const submittedCount = items.filter((a) => a.status === "submitted").length;
  const inProgressCount = items.filter((a) => a.status === "in_progress" || a.status === "filled").length;

  return (
    <div className="space-y-8">
      <section className="rounded-2xl bg-neutral-900 px-8 py-10 text-white">
        <h1 className="text-2xl font-semibold">
          Welcome, {user?.firstName ?? "there"}
        </h1>
        <p className="mt-1 text-neutral-300">
          Here&apos;s where your applications stand.
        </p>
        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatCard label="Total applications" value={data?.total ?? 0} />
          <StatCard label="In progress" value={inProgressCount} />
          <StatCard label="Submitted" value={submittedCount} />
        </div>
      </section>

      <section className="rounded-2xl border border-neutral-200 bg-white">
        <div className="flex items-center justify-between border-b border-neutral-200 px-6 py-4">
          <h2 className="font-semibold">Recent applications</h2>
          <Link href="/resumes" className="text-sm text-neutral-500 hover:text-neutral-900">
            Manage resumes →
          </Link>
        </div>

        {isLoading && <div className="px-6 py-8 text-sm text-neutral-500">Loading…</div>}

        {!isLoading && items.length === 0 && (
          <div className="px-6 py-8 text-sm text-neutral-500">
            No applications yet. They&apos;ll show up here once the extension
            detects a job form and starts filling it.
          </div>
        )}

        {items.length > 0 && (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-neutral-200 text-left text-neutral-500">
                <th className="px-6 py-3 font-medium">Platform</th>
                <th className="px-6 py-3 font-medium">Status</th>
                <th className="px-6 py-3 font-medium">Fields filled</th>
                <th className="px-6 py-3 font-medium">AI answers</th>
                <th className="px-6 py-3 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {items.map((app) => (
                <tr key={app.id} className="border-b border-neutral-100 last:border-0">
                  <td className="px-6 py-3 capitalize">{app.platform}</td>
                  <td className="px-6 py-3">
                    <span
                      className={`rounded-full px-2.5 py-1 text-xs font-medium ${STATUS_STYLES[app.status] ?? "bg-neutral-100 text-neutral-700"}`}
                    >
                      {app.status.replace("_", " ")}
                    </span>
                  </td>
                  <td className="px-6 py-3">{app.fields_filled_count}</td>
                  <td className="px-6 py-3">{app.ai_answers_count}</td>
                  <td className="px-6 py-3 text-neutral-500">
                    {new Date(app.created_at).toLocaleDateString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-white/10 px-5 py-4">
      <div className="text-2xl font-semibold">{value}</div>
      <div className="text-sm text-neutral-300">{label}</div>
    </div>
  );
}
