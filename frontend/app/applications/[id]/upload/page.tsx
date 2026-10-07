"use client";

import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { ACTIVE_DOC_STATUSES, useApi } from "@/lib/hooks";
import { DocumentsTable } from "@/components/DocumentsTable";
import { RequirementChecklist } from "@/components/RequirementChecklist";
import { UploadPanel } from "@/components/UploadPanel";
import { Card, ErrorBanner, Loading } from "@/components/ui";

export default function UploadPage() {
  const { id } = useParams<{ id: string }>();
  const docs = useApi(() => api.documents(id), [id], {
    shouldPoll: (d) => d.some((doc) => ACTIVE_DOC_STATUSES.has(doc.document_status)),
  });
  const processing = docs.data?.some((d) => ACTIVE_DOC_STATUSES.has(d.document_status)) ?? false;
  // completeness refreshes whenever the document list changes
  const comp = useApi(() => api.completeness(id), [id, JSON.stringify(docs.data?.map((d) => d.document_status))]);

  return (
    <div className="space-y-4">
      <Card title="Upload documents">
        <UploadPanel applicationId={id} onUploaded={() => docs.reload()} />
      </Card>
      <Card title="Document checklist"
        actions={comp.data && (
          <span className="text-xs text-slate-500">
            {comp.data.summary.required_received}/{comp.data.summary.required_total} required received
            · {comp.data.summary.completeness_pct}%
          </span>
        )}>
        <ErrorBanner message={comp.error} />
        {comp.data ? <RequirementChecklist completeness={comp.data} /> : <Loading />}
      </Card>
      <Card title="Uploaded documents" actions={processing && <span className="animate-pulse text-xs text-sky-700">Processing…</span>}>
        <ErrorBanner message={docs.error} />
        {docs.data ? <DocumentsTable documents={docs.data} /> : <Loading />}
      </Card>
    </div>
  );
}
