"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { ACTIVE_DOC_STATUSES, useApi } from "@/lib/hooks";
import { humanize } from "@/lib/format";
import { PipelineList } from "@/components/PipelineProgress";
import { Button, Card, Empty, ErrorBanner, Loading, StatusBadge } from "@/components/ui";

export default function ProcessingPage() {
  const { id } = useParams<{ id: string }>();
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const { data, error, loading, reload } = useApi(() => api.documents(id), [id], {
    shouldPoll: (d) => d.some((doc) => ACTIVE_DOC_STATUSES.has(doc.document_status)),
  });

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
      reload();
    }
  };

  if (loading && !data) return <Loading />;
  const docs = data ?? [];
  const counts = docs.reduce<Record<string, number>>((acc, d) => ({ ...acc, [d.document_status]: (acc[d.document_status] ?? 0) + 1 }), {});

  return (
    <div className="space-y-4">
      <ErrorBanner message={error ?? actionError} />
      <Card title="Processing dashboard"
        actions={<>
          <Button variant="secondary" disabled={!!busy} onClick={() => run("process", () => api.processAll(id))}>
            {busy === "process" ? "Processing…" : "Process pending / failed"}
          </Button>
          <Button variant="secondary" disabled={!!busy} onClick={() => run("reconcile", () => api.reconcile(id))}>
            {busy === "reconcile" ? "Running…" : "Re-run reconciliation"}
          </Button>
        </>}>
        <div className="flex flex-wrap gap-3 text-sm">
          {Object.entries(counts).map(([s, n]) => (
            <span key={s} className="flex items-center gap-1.5"><StatusBadge status={s} /> <span className="tabular-nums">{n}</span></span>
          ))}
          {!docs.length && <Empty>No documents uploaded.</Empty>}
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Every stage writes a processing record. Re-processing replaces a document&apos;s derived data (no duplicates) and keeps the run history.
        </p>
      </Card>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {docs.map((d) => (
          <Card key={d.id}
            title={<Link href={`/documents/${d.id}`} className="hover:underline">{d.document_code} · {d.filename}</Link>}
            actions={<StatusBadge status={d.document_status} />}>
            <div className="mb-2 flex items-center justify-between text-xs text-slate-500">
              <span>{humanize(d.document_type)} · run #{d.processing_run}</span>
              {!d.is_duplicate && (
                <button className="text-indigo-700 hover:underline disabled:text-slate-400"
                  disabled={!!busy || ACTIVE_DOC_STATUSES.has(d.document_status) && d.document_status === "PROCESSING"}
                  onClick={() => run(d.id, () => api.process(d.id))}>
                  Re-process
                </button>
              )}
            </div>
            {d.pipeline && <PipelineList stages={d.pipeline} />}
            {d.error_message && <div className="mt-2 break-words rounded bg-rose-50 p-2 text-xs text-rose-800">{d.error_message}</div>}
          </Card>
        ))}
      </div>
    </div>
  );
}
