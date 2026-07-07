import { ConversationsPanel } from "@/components/ConversationsPanel";

import { logout } from "./actions";

export default function ConversasPage() {
  return (
    <div className="flex h-screen overflow-hidden">
      <nav className="flex w-14 shrink-0 flex-col items-center justify-between bg-ink py-5">
        <span className="font-display text-2xl font-semibold text-ground" aria-label="Advoxs">
          A.
        </span>
        <form action={logout}>
          <button
            type="submit"
            className="font-mono text-[10px] uppercase tracking-[0.2em] text-ground/60 transition-colors [writing-mode:vertical-rl] hover:text-ground"
          >
            Sair
          </button>
        </form>
      </nav>
      <ConversationsPanel />
    </div>
  );
}
