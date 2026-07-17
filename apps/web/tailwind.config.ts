import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/**/*.{ts,tsx}",
    "../../packages/ui/src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ground: "var(--ground)",
        surface: "var(--surface)",
        ink: "var(--text)",
        muted: "var(--muted)",
        line: "var(--line)",
        accent: "var(--accent)",
        "accent-soft": "var(--accent-soft)",
        brass: "var(--brass)",
        "brass-soft": "var(--brass-soft)",
        danger: "var(--danger)",
        "nav-bg": "var(--nav-bg)",
        "nav-bg-2": "var(--nav-bg-2)",
        "nav-active": "var(--nav-active)",
        "nav-ink": "var(--nav-ink)",
        "nav-ink-muted": "var(--nav-ink-muted)",
      },
      fontFamily: {
        display: ["var(--font-spectral)", "Georgia", "serif"],
        sans: ["var(--font-plex-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-plex-mono)", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};

export default config;
