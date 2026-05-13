import Link from "next/link";
import type { Metadata } from "next";
import "../styles/globals.css";
import { ReactQueryProvider } from "./_providers";
import { SiteFooter } from "@/components/SiteFooter";

export const metadata: Metadata = {
  title: "Assembly — Multi-Agent AI Decision Intelligence",
  description:
    "Spawn dozens of evidence-anchored AI agents that debate your product or decision, shift opinions across rounds, and converge on a Meta Report of where consensus is actually headed.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <ReactQueryProvider>
          <div className="flex min-h-screen flex-col px-6 py-8">
            <header className="mb-12 flex items-center justify-between border-b border-border pb-4">
              <Link
                href="/"
                aria-label="Assembly"
                className="inline-flex items-center gap-3 leading-none"
              >
                <span
                  aria-hidden
                  className="brand-bracket text-2xl font-bold"
                >
                  [
                </span>
                <span className="brand-wordmark text-3xl font-extrabold uppercase tracking-[0.04em]">
                  Assembly
                </span>
                <span
                  aria-hidden
                  className="brand-bracket text-2xl font-bold"
                >
                  ]
                </span>
              </Link>
              <nav
                aria-label="Primary"
                className="flex items-center gap-5 text-sm text-text-body"
              >
                <Link
                  href="/sample-report"
                  className="transition-colors hover:text-accent"
                >
                  Sample report
                </Link>
                <Link
                  href="/contact"
                  className="rounded-md border border-border bg-surface px-4 py-1.5 text-text-body transition-colors hover:border-accent-border hover:text-accent"
                >
                  Contact us
                </Link>
              </nav>
            </header>
            <main className="flex-1">{children}</main>
            <SiteFooter />
          </div>
        </ReactQueryProvider>
      </body>
    </html>
  );
}
