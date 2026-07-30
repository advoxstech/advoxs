import { LoginAside } from "@/components/LoginAside";

import { LoginForm } from "./LoginForm";

export default function LoginPage() {
  return (
    <div className="grid min-h-screen grid-cols-1 lg:grid-cols-[0.85fr_1fr]">
      <LoginAside />
      <main className="flex items-center justify-center px-6 py-14 lg:px-16">
        <LoginForm />
      </main>
    </div>
  );
}
