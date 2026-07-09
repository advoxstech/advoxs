import { AdminLoginForm } from "@/components/AdminLoginForm";

export default function AdminLoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-6">
      <div className="w-full max-w-sm">
        <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-muted">
          Back-office Advoxs
        </p>
        <h1 className="mt-2 font-display text-5xl font-semibold text-ink">
          Admin<span className="text-accent">.</span>
        </h1>

        <hr className="my-8 border-line" />

        <AdminLoginForm />
      </div>
    </main>
  );
}
