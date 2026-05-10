import type { Config } from "tailwindcss";

// Phase 10B — locked dark premium AI lab palette.
// Driven by CSS variables in src/styles/tokens.css so design tokens
// stay in one place and tests can assert on the literal hex values.
//
// The legacy "ink/warn/accent.subtle" tokens are kept ONLY to avoid
// build breakage in older Phase-7 simulations/ pages — they are not
// the 10B-locked palette and must not be used in new components.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // 10B locked palette — driven by CSS variables in tokens.css
        background: "var(--background)",
        surface: "var(--surface)",
        "surface-elevated": "var(--surface-elevated)",
        border: "var(--border)",
        "text-primary": "var(--text-primary)",
        "text-body": "var(--text-body)",
        "text-muted": "var(--text-muted)",
        accent: {
          DEFAULT: "var(--accent)",
          soft: "var(--accent-soft)",
          border: "var(--accent-border)",
          // legacy alias used by old Phase-7 components
          subtle: "rgba(170, 255, 0, 0.08)",
        },
        danger: "var(--danger)",
        warning: "var(--warning)",
        success: "var(--success)",
        // ---- Legacy Phase-7 palette (do not use in new code) -----
        ink: {
          50: "#fafaf9",
          100: "#f4f4f3",
          200: "#e6e6e3",
          400: "#9b9b95",
          600: "#5a5a55",
          800: "#2b2b29",
          900: "#161614",
        },
        warn: {
          DEFAULT: "#8b5a1c",
          subtle: "#f5e6d2",
        },
      },
      fontFamily: {
        serif: ['"Source Serif Pro"', "Georgia", "serif"],
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', "monospace"],
      },
      boxShadow: {
        "accent-glow":
          "0 0 0 1px var(--accent-border), 0 0 24px -8px var(--accent)",
      },
    },
  },
  plugins: [],
};

export default config;
