import { Nav } from "@/components/nav";
import { EnsureSynced } from "@/components/ensure-synced";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <EnsureSynced />
      <Nav />
      <main className="flex-1 bg-neutral-50 px-6 py-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
