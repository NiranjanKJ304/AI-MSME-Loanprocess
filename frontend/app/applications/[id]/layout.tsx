"use client";

import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatINR, humanize } from "@/lib/format";
import { ErrorBanner, StatusBadge } from "@/components/ui";

const TABS = [
  { href: "", label: "Overview" },
  { href: "/upload", label: "Documents & upload" },
  { href: "/processing", label: "Processing" },
  { href: "/validation", label: "Validation & reconciliation" },
  { href: "/audit", label: "Audit log" },
];

export default function ApplicationLayout({ children }: { children: React.ReactNode }) {
  const { id } = useParams<{ id: string }>();
  const pathname = usePathname();
  const { data: app, error } = useApi(() => api.getApplication(id), [id, pathname], {
    pollMs: 3000,
    shouldPoll: (a) => a.status === "PROCESSING",
  });
  const base = `/applications/${id}`;

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} />
      {app && (
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <div className="text-xs font-medium text-slate-500">{app.application_number}</div>
            <h1 className="text-xl font-semibold text-slate-900">{app.business_name}</h1>
            <div className="mt-0.5 text-sm text-slate-600">
              {humanize(app.applicant_type)} · {humanize(app.loan_type)} · {formatINR(app.requested_amount)}
              {app.loan_purpose && <> · <span className="text-slate-500">{app.loan_purpose}</span></>}
            </div>
          </div>
          <StatusBadge status={app.status} />
        </div>
      )}
      <nav className="flex flex-wrap gap-1 border-b border-slate-200">
        {TABS.map((t) => {
          const href = base + t.href;
          const active = t.href === "" ? pathname === base : pathname.startsWith(href);
          return (
            <Link key={t.href} href={href}
              className={`-mb-px border-b-2 px-3 py-2 text-sm ${active ? "border-indigo-700 font-medium text-indigo-800" : "border-transparent text-slate-600 hover:text-slate-900"}`}>
              {t.label}
            </Link>
          );
        })}
      </nav>
      {children}
    </div>
  );
}
