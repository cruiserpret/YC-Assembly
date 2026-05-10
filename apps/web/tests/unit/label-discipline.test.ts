import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

/**
 * Phase 8 quality gate — UI label discipline.
 *
 * Walk every .ts/.tsx source under apps/web/src and assert that none of
 * the forbidden words / phrases appear inside JSX text or string-literal
 * children. The same rules the backend's `REAL_WORLD_INSTRUCTIONS`
 * validator enforces on persisted output apply here, plus a stricter
 * verdict-style ban for UI labels.
 *
 * Comments and identifier names are allowed (file paths, function names,
 * etc.). The check is intentionally line-based and case-insensitive: a
 * forbidden phrase appearing inside a string literal or JSX child anywhere
 * in the file fails.
 */

const SRC_ROOT = join(__dirname, "..", "..", "src");

// Patterns catch DIRECTIVE / forecast-asserting phrasing, not the words
// themselves used in clear negation. "It does NOT forecast" and
// "qualitative — not a forecast" are exactly the disclaimers the UI must
// surface, so a bare /\bforecast\b/ false-positives on them.
const FORBIDDEN_PATTERNS: { name: string; re: RegExp }[] = [
  { name: "verdict.predict_X", re: /\b(?:we\s+)?predict\s+(?:that|the|conversion|CTR|CAC|revenue)\b/i },
  { name: "verdict.we_forecast", re: /\b(?:we|will|the\s+model)\s+forecast(?:s|ing|ed)?\b/i },
  { name: "verdict.guaranteed_to", re: /\bguaranteed\s+to\b/i },
  { name: "verdict.success_probability", re: /\bsuccess\s+probability\b/i },
  { name: "verdict.market_winner", re: /\bmarket\s+winner\b/i },
  { name: "verdict.kill_the_test", re: /\bkill\s+(?:the\s+)?(?:test|campaign|ad\s+set|ads)\b/i },
  { name: "verdict.spend_on_ads", re: /\bspend\s+\$\s*\d/i },
  { name: "rwi.run_ads", re: /\brun\s+(?:Meta|Google|TikTok|Facebook|LinkedIn|Reddit)\s+ads\b/i },
  { name: "rwi.launch_landing_page", re: /\blaunch\s+(?:a\s+)?landing\s+page\b/i },
  { name: "rwi.validation_campaign", re: /\b(?:run|launch|start)\s+(?:a\s+)?validation\s+campaign\b/i },
];

// Source files we deliberately exempt — they store the rule list itself,
// or are explicitly the debug-mode escape hatch where raw payloads can be
// inspected.
const EXEMPT_SUFFIXES: string[] = [
  // The label-discipline test itself has these patterns by definition.
  // (This file lives outside src/ so it isn't scanned, but the gate is
  // documented here.)
];

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const s = statSync(full);
    if (s.isDirectory()) {
      yield* walk(full);
    } else if (full.endsWith(".ts") || full.endsWith(".tsx")) {
      yield full;
    }
  }
}

describe("UI label discipline", () => {
  it("contains no forbidden verdict / forecast / real-world-instruction phrases", () => {
    const offenders: string[] = [];
    for (const file of walk(SRC_ROOT)) {
      if (EXEMPT_SUFFIXES.some((s) => file.endsWith(s))) continue;
      const text = readFileSync(file, "utf-8");
      // Strip line comments + block comments before scanning.
      const stripped = text
        .replace(/\/\*[\s\S]*?\*\//g, "")
        .replace(/(^|[^:])\/\/.*$/gm, "$1");
      for (const pat of FORBIDDEN_PATTERNS) {
        const m = stripped.match(pat.re);
        if (m) {
          offenders.push(`${file}: ${pat.name} matched ${JSON.stringify(m[0])}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
