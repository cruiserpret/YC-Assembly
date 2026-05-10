import type { Metadata } from "next";
import "../styles/globals.css";
import { ReactQueryProvider } from "./_providers";

export const metadata: Metadata = {
  title: "Assembly · synthetic-society simulation lab",
  description:
    "Submit a product brief. Assembly builds a synthetic society from real evidence, lets it react and debate, and reports who's receptive, who resists, and what to test next. Synthetic — not a market forecast.",
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
          <div className="px-6 py-8">
            <header className="mb-12 flex items-baseline justify-between border-b border-border pb-4">
              <a
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
              </a>
              <span className="text-xs uppercase tracking-widest text-text-muted">
                synthetic-society simulation lab
              </span>
            </header>
            <main>{children}</main>
          </div>
        </ReactQueryProvider>
      </body>
    </html>
  );
}
