import type { Metadata } from "next";
import localFont from "next/font/local";

import "./globals.css";

const spectral = localFont({
  src: [
    { path: "../fonts/spectral-medium.woff", weight: "500", style: "normal" },
    { path: "../fonts/spectral-semibold.woff", weight: "600", style: "normal" },
  ],
  variable: "--font-spectral",
  display: "swap",
});

const plexSans = localFont({
  src: [
    { path: "../fonts/ibm-plex-sans-regular.woff2", weight: "400", style: "normal" },
    { path: "../fonts/ibm-plex-sans-medium.woff2", weight: "500", style: "normal" },
    { path: "../fonts/ibm-plex-sans-semibold.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex-sans",
  display: "swap",
});

const plexMono = localFont({
  src: [
    { path: "../fonts/ibm-plex-mono-regular.woff2", weight: "400", style: "normal" },
    { path: "../fonts/ibm-plex-mono-medium.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-plex-mono",
  display: "swap",
});

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
      <body
        className={`${spectral.variable} ${plexSans.variable} ${plexMono.variable} font-sans antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
