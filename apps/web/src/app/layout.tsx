import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Advoxs",
  description: "Plataforma de agentes de IA para escritórios de advocacia",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="pt-BR">
      <body>{children}</body>
    </html>
  );
}
