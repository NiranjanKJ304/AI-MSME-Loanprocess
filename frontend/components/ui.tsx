import type { ReactNode } from "react";
import { humanize } from "@/lib/format";

export function Card({ title, actions, children, className = "" }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-slate-200 bg-white shadow-sm ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
          <h2 className="text-sm font-semibold text-slate-800">{title}</h2>
          <div className="flex items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const TONES: Record<string, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  red: "bg-rose-50 text-rose-700 ring-rose-200",
  blue: "bg-sky-50 text-sky-700 ring-sky-200",
  slate: "bg-slate-100 text-slate-600 ring-slate-200",
  violet: "bg-violet-50 text-violet-700 ring-violet-200",
};

const STATUS_TONE: Record<string, keyof typeof TONES> = {
  PROCESSED: "green", VALID: "green", VALIDATED: "green", PASS: "green", COMPLETED: "green", RECEIVED: "green",
  NEEDS_REVIEW: "amber", COMPLETED_WITH_WARNINGS: "amber", RECEIVED_NEEDS_REVIEW: "amber", WARNING: "amber",
  OFFICER_TO_CONFIRM: "violet", INCONSISTENCY: "amber",
  LOW_CONFIDENCE: "amber", AMBIGUOUS: "amber", OCR_REQUIRED: "violet", EXTRACTION_FAILED: "red", UNSUPPORTED: "slate",
  NOT_FOUND: "slate", HYBRID: "blue",
  INVALID: "red", FAILED: "red", FAIL: "red", MISSING: "red", ERROR: "red", RECEIVED_INVALID: "red",
  PROCESSING: "blue", RUNNING: "blue", VALIDATING: "blue", UPLOADED: "blue", EXTRACTED: "blue",
  PENDING_PROCESSING: "blue", DOCUMENTS_RECEIVED: "blue",
  SKIPPED: "slate", PENDING: "slate", INSUFFICIENT_DATA: "slate", NOT_APPLICABLE: "slate", INFO: "slate",
  CREATED: "slate",
};

export function Badge({ children, tone = "slate", title }: { children: ReactNode; tone?: keyof typeof TONES; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export function StatusBadge({ status, title }: { status: string | null | undefined; title?: string }) {
  if (!status) return <Badge>—</Badge>;
  return (
    <Badge tone={STATUS_TONE[status] ?? "slate"} title={title}>
      {humanize(status)}
    </Badge>
  );
}

export function ConfidenceBar({ value, threshold = 0.7 }: { value: number | null | undefined; threshold?: number }) {
  if (value === null || value === undefined) return <span className="text-xs text-slate-400">—</span>;
  const pct = Math.round(value * 100);
  const color = value >= 0.85 ? "bg-emerald-500" : value >= threshold ? "bg-sky-500" : "bg-amber-500";
  return (
    <div className="flex items-center gap-2" title={`Confidence ${pct}%${value < threshold ? " (below review threshold)" : ""}`}>
      <div className="h-1.5 w-16 overflow-hidden rounded bg-slate-200">
        <div className={`h-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`text-xs tabular-nums ${value < threshold ? "font-semibold text-amber-700" : "text-slate-600"}`}>{pct}%</span>
    </div>
  );
}

export function StatTile({ label, value, sub, tone = "slate" }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "slate" | "green" | "amber" | "red";
}) {
  const accent = { slate: "border-l-slate-300", green: "border-l-emerald-500", amber: "border-l-amber-500", red: "border-l-rose-500" }[tone];
  return (
    <div className={`rounded-lg border border-slate-200 border-l-4 ${accent} bg-white p-3 shadow-sm`}>
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-slate-900">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </div>
  );
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">{message}</div>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="py-8 text-center text-sm text-slate-500">{label}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-6 text-center text-sm text-slate-500">{children}</div>;
}

export function Button({ children, onClick, disabled, variant = "primary", type = "button" }: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary";
  type?: "button" | "submit";
}) {
  const cls = variant === "primary"
    ? "bg-indigo-700 text-white hover:bg-indigo-800 disabled:bg-slate-300"
    : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 disabled:text-slate-400";
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`rounded-md px-3 py-1.5 text-sm font-medium shadow-sm transition ${cls}`}>
      {children}
    </button>
  );
}

export function Table({ head, children }: { head: ReactNode[]; children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-slate-200 text-sm">
        <thead>
          <tr className="text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
            {head.map((h, i) => <th key={i} className="whitespace-nowrap px-2 py-2">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100">{children}</tbody>
      </table>
    </div>
  );
}
