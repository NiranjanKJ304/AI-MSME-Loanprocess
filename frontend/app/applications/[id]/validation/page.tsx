"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { humanize } from "@/lib/format";
import { ReconciliationList } from "@/components/ReconciliationList";
import { Badge, Card, Empty, ErrorBanner, Loading, StatusBadge, Table } from "@/components/ui";

export default function ValidationPage() {
  const { id } = useParams<{ id: string }>();
  const val = useApi(() => api.validation(id), [id]);
  const rec = useApi(() => api.reconciliation(id), [id]);
  const [severity, setSeverity] = useState<string>("ALL");

  const results = (val.data?.results ?? []).filter((r) => severity === "ALL" || r.severity === severity);

  return (
    <div className="space-y-4">
      <ErrorBanner message={val.error ?? rec.error} />
      <Card title="Cross-document reconciliation"
        actions={rec.data && Object.entries(rec.data.summary).map(([k, v]) => <span key={k} className="flex items-center gap-1 text-xs"><StatusBadge status={k} />{v}</span>)}>
        {rec.data ? (
          <>
            <p className="mb-2 text-xs text-slate-500">{rec.data.note}</p>
            <ReconciliationList results={rec.data.results} />
          </>
        ) : <Loading />}
      </Card>

      {val.data && val.data.file_issues.length > 0 && (
        <Card title="Invalid / failed files">
          <ul className="space-y-1 text-sm">
            {val.data.file_issues.map((f) => (
              <li key={f.document_id}>
                <Link href={`/documents/${f.document_id}`} className="font-medium text-indigo-700 hover:underline">{f.document_code} · {f.filename}</Link>
                <span className="ml-2"><StatusBadge status={f.status} /></span>
                <div className="break-words text-xs text-rose-800">{f.error}</div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title={`Validation findings (${val.data?.summary.total ?? 0})`}
        actions={
          <select className="rounded border border-slate-300 px-2 py-1 text-xs" value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="ALL">All severities</option>
            <option value="ERROR">Errors</option>
            <option value="WARNING">Warnings</option>
          </select>
        }>
        {!val.data ? <Loading /> : results.length === 0 ? <Empty>No validation findings.</Empty> : (
          <Table head={["Document", "Scope", "Rule", "Status", "Severity", "Finding", "Page"]}>
            {results.map((r) => (
              <tr key={r.id} className="align-top">
                <td className="whitespace-nowrap px-2 py-1.5">
                  <Link href={`/documents/${r.document_id}`} className="font-mono text-xs text-indigo-700 hover:underline">{r.document_code}</Link>
                </td>
                <td className="px-2 py-1.5 text-xs text-slate-500">{humanize(r.scope)}{r.field_name ? ` · ${r.field_name}` : ""}</td>
                <td className="whitespace-nowrap px-2 py-1.5 text-xs font-medium">{r.rule_code}</td>
                <td className="px-2 py-1.5"><StatusBadge status={r.status} /></td>
                <td className="px-2 py-1.5"><Badge tone={r.severity === "ERROR" ? "red" : r.severity === "WARNING" ? "amber" : "slate"}>{r.severity}</Badge></td>
                <td className="px-2 py-1.5 text-xs text-slate-700">{r.message}</td>
                <td className="px-2 py-1.5 text-xs tabular-nums text-slate-500">{r.source_page ?? "—"}</td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
