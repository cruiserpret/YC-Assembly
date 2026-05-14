// Phase 10B.7 — site-wide footer.
//
// Minimal three-link footer + contact mailto + copyright. Rendered
// from the root layout, so every public page (landing, sample
// report, contact, privacy, terms, run detail) gets it without
// per-page wiring.

import Link from "next/link";

export function SiteFooter() {
  const year = new Date().getFullYear();
  return (
    <footer
      data-testid="site-footer"
      className="mt-16 border-t border-border pt-6 text-xs text-text-muted"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <nav
          aria-label="Footer"
          className="flex flex-wrap items-center gap-5"
        >
          <Link
            href="/privacy"
            className="transition-colors hover:text-accent"
          >
            Privacy
          </Link>
          <Link
            href="/terms"
            className="transition-colors hover:text-accent"
          >
            Terms
          </Link>
          <Link
            href="/contact"
            className="transition-colors hover:text-accent"
          >
            Contact
          </Link>
        </nav>
        <p className="font-mono">
          © {year} Assembly. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
