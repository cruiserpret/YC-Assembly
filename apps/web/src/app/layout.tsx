import Link from "next/link";
import type { Metadata } from "next";
import "../styles/globals.css";
import { ReactQueryProvider } from "./_providers";
import { SiteFooter } from "@/components/SiteFooter";

export const metadata: Metadata = {
  title: "Assembly — Multi-Agent AI Decision Intelligence",
  description:
    "Submit a product brief. Assembly builds an evidence-grounded room of synthetic personas, runs a multi-round simulation, and returns a Market Reaction Report — who leans in, who pushes back, and why.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen overflow-x-hidden antialiased">
        <ReactQueryProvider>
          <div className="flex min-h-screen flex-col px-4 py-8 sm:px-6 lg:px-12">
            <header className="mb-12 flex flex-wrap items-center justify-between gap-4 border-b border-border pb-4">
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
                className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm text-text-body"
              >
                <Link
                  href="/#product"
                  className="transition-colors hover:text-accent"
                >
                  Product
                </Link>
                <Link
                  href="/sample-report"
                  className="transition-colors hover:text-accent"
                >
                  Sample Report
                </Link>
                <Link
                  href="/#submit-brief"
                  className="transition-colors hover:text-accent"
                >
                  Run Simulation
                </Link>
                <Link
                  href="/contact"
                  className="rounded-md border border-border bg-surface px-4 py-1.5 text-text-body transition-colors hover:border-accent-border hover:text-accent"
                >
                  Contact
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
