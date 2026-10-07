"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, formatINR, humanize } from "@/lib/format";
import { Card, Empty, ErrorBanner, Loading, StatusBadge, Table } from "@/components/ui";

export default function ApplicationsPage() {
  const { data, error, loading } = useApi(() => api.listApplications(), [], {
    pollMs: 4000,
    shouldPoll: (apps) => apps.some((a) => a.status === "PROCESSING"),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-900">Loan applications</h1>
        <Link href="/applications/new" className="rounded-md bg-indigo-700 px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-indigo-800">
          + New application
        </Link>
      </div>
      <ErrorBanner message={error} />
      <Card>
        {loading && !data ? <Loading /> : !data?.length ? (
          <Empty>No applications yet. Create one to start uploading documents.</Empty>
        ) : (
          <Table head={["Application", "Business", "Applicant", "Loan", "Amount", "Documents", "Status", "Created"]}>
            {data.map((a) => (
              <tr key={a.id} className="hover:bg-slate-50">
                <td className="px-2 py-2">
                  <Link href={`/applications/${a.id}`} className="font-medium text-indigo-700 hover:underline">{a.application_number}</Link>
                </td>
                <td className="px-2 py-2">{a.business_name}</td>
                <td className="px-2 py-2 text-slate-600">{humanize(a.applicant_type)}</td>
                <td className="px-2 py-2 text-slate-600">{humanize(a.loan_type)}</td>
                <td className="px-2 py-2 tabular-nums">{formatINR(a.requested_amount)}</td>
                <td className="px-2 py-2 text-slate-600">
                  {a.document_count}
                  {a.documents_needing_review > 0 && <span className="ml-1 text-amber-700">({a.documents_needing_review} review)</span>}
                  {a.documents_failed > 0 && <span className="ml-1 text-rose-700">({a.documents_failed} failed/invalid)</span>}
                </td>
                <td className="px-2 py-2"><StatusBadge status={a.status} /></td>
                <td className="px-2 py-2 text-xs text-slate-500">{formatDateTime(a.created_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
