import Link from "next/link";
import type { DocumentSummary } from "@/types";
import { humanize, pct } from "@/lib/format";
import { PipelineStrip } from "@/components/PipelineProgress";
import { Empty, StatusBadge, Table } from "@/components/ui";

export function DocumentsTable({ documents }: { documents: DocumentSummary[] }) {
  if (!documents.length) return <Empty>No documents uploaded yet.</Empty>;
  return (
    <Table head={["Ref", "File", "Detected type", "Status", "Pipeline", "Pages", "Extraction"]}>
      {documents.map((d) => (
        <tr key={d.id} className="align-top hover:bg-slate-50">
          <td className="whitespace-nowrap px-2 py-2 font-mono text-xs text-slate-600">{d.document_code}</td>
          <td className="max-w-[16rem] px-2 py-2">
            <Link href={`/documents/${d.id}`} className="break-words font-medium text-indigo-700 hover:underline">{d.filename}</Link>
            {d.is_duplicate && <div className="text-xs text-rose-700">Duplicate upload</div>}
          </td>
          <td className="whitespace-nowrap px-2 py-2">
            <div>{humanize(d.document_type)}</div>
            <div className="text-xs text-slate-500">
              {d.classification_confidence !== null ? `${pct(d.classification_confidence)} · ${d.classification_method}` : "not classified"}
            </div>
          </td>
          <td className="max-w-[18rem] px-2 py-2">
            <StatusBadge status={d.document_status} />
            {d.error_message && <div className="mt-1 break-words text-xs text-rose-700">{d.error_message}</div>}
            {!d.error_message && d.status_reasons?.length ? (
              <div className="mt-1 text-xs text-amber-800">{d.status_reasons.slice(0, 3).map(humanize).join(" · ")}
                {d.status_reasons.length > 3 && ` +${d.status_reasons.length - 3} more`}</div>
            ) : null}
          </td>
          <td className="px-2 py-2"><PipelineStrip stages={d.pipeline} /></td>
          <td className="px-2 py-2 tabular-nums text-slate-600">{d.page_count ?? "—"}</td>
          <td className="px-2 py-2 tabular-nums text-slate-600">{pct(d.extraction_confidence)}</td>
        </tr>
      ))}
    </Table>
  );
}
