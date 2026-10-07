"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, humanize } from "@/lib/format";
import { Card, Empty, ErrorBanner, Loading, StatusBadge, Table } from "@/components/ui";

export default function AuditPage() {
  const { id } = useParams<{ id: string }>();
  const { data, error, loading } = useApi(() => api.audit(id), [id]);

  return (
    <Card title="Audit log (newest first, append-only)">
      <ErrorBanner message={error} />
      {loading && !data ? <Loading /> : !data?.length ? <Empty>No audit entries.</Empty> : (
        <Table head={["Time", "Action", "Stage", "Status", "Document", "Actor", "Detail"]}>
          {data.map((a) => (
            <tr key={a.id} className="align-top">
              <td className="whitespace-nowrap px-2 py-1.5 text-xs text-slate-500">{formatDateTime(a.timestamp)}</td>
              <td className="whitespace-nowrap px-2 py-1.5 text-xs font-medium">{humanize(a.action)}</td>
              <td className="whitespace-nowrap px-2 py-1.5 text-xs text-slate-500">{a.stage ? humanize(a.stage) : "—"}</td>
              <td className="px-2 py-1.5"><StatusBadge status={a.status} /></td>
              <td className="px-2 py-1.5 text-xs">
                {a.document_id ? <Link href={`/documents/${a.document_id}`} className="font-mono text-indigo-700 hover:underline">{a.document_id.slice(0, 8)}</Link> : "—"}
              </td>
              <td className="px-2 py-1.5 text-xs text-slate-500">{a.actor}</td>
              <td className="max-w-md break-words px-2 py-1.5 text-xs text-slate-600">
                {a.error && <div className="text-rose-700">{a.error}</div>}
                {a.details && <code className="text-[11px] text-slate-500">{JSON.stringify(a.details)}</code>}
                {a.source && <code className="block text-[11px] text-slate-400">{JSON.stringify(a.source)}</code>}
              </td>
            </tr>
          ))}
        </Table>
      )}
    </Card>
  );
}
