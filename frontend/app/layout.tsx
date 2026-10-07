import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "MSME Loan Document Intelligence (Prototype)",
  description: "Document ingestion, extraction, validation and traceability for MSME loan applications.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="border-b border-slate-800 bg-slate-900 text-white">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
            <Link href="/" className="flex items-baseline gap-2">
              <span className="text-base font-semibold tracking-tight">MSME Loan Document Intelligence</span>
              <span className="rounded bg-amber-400 px-1.5 py-0.5 text-[10px] font-bold uppercase text-slate-900">Prototype</span>
            </Link>
            <nav className="flex gap-4 text-sm text-slate-300">
              <Link href="/" className="hover:text-white">Applications</Link>
              <Link href="/applications/new" className="hover:text-white">New application</Link>
            </nav>
          </div>
        </header>
        <div className="border-b border-amber-200 bg-amber-50">
          <p className="mx-auto max-w-7xl px-4 py-1.5 text-xs text-amber-900">
            Decision-support prototype for document processing only. It does not score, approve or reject loans;
            every flagged item requires officer review.
          </p>
        </div>
        <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
