"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Fragment } from "react";
import { api } from "@/lib/api";
import { ACTIVE_DOC_STATUSES, useApi } from "@/lib/hooks";
import { humanize, pct } from "@/lib/format";
import { DocumentsTable } from "@/components/DocumentsTable";
import { ReconciliationList } from "@/components/ReconciliationList";
import { Card, Empty, ErrorBanner, Loading, StatTile } from "@/components/ui";

export default function OverviewPage() {
  const { id } = useParams<{ id: string }>();
  const { data: o, error, loading } = useApi(() => api.overview(id), [id], {
    shouldPoll: (d) => d.documents.some((doc) => ACTIVE_DOC_STATUSES.has(doc.document_status)),
  });

  if (loading && !o) return <Loading />;
  if (!o) return <ErrorBanner message={error} />;
  const q = o.extraction_quality;
  const ev = o.evidence_coverage;
  const missing = o.documents_missing.required_missing;

  return (
    <div className="space-y-4">
      <ErrorBanner message={error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
        <StatTile label="Documents received" value={o.documents_received.unique}
          sub={o.documents_received.duplicates ? `${o.documents_received.duplicates} duplicate(s) rejected` : `${o.documents_received.total_uploaded} uploaded`} />
        <StatTile label="Required missing" value={missing.length} tone={missing.length ? "red" : "green"}
          sub={`${o.documents_missing.completeness_pct}% of required received`} />
        <StatTile label="Extraction quality" value={pct(q.average_document_confidence)}
          tone={q.average_document_confidence >= 0.85 ? "green" : "amber"}
          sub={`${q.fields_extracted}/${q.fields_total} fields · ${q.low_confidence_fields} low-conf.`} />
        <StatTile label="Validation issues" value={o.validation_issues.total} tone={o.validation_issues.total ? "amber" : "green"}
          sub={Object.entries(o.validation_issues.by_severity).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(" · ") || "none"} />
        <StatTile label="Cross-doc inconsistencies" value={o.cross_document.inconsistencies}
          tone={o.cross_document.inconsistencies ? "amber" : "green"}
          sub={`${o.cross_document.passed} passed · ${o.cross_document.insufficient_data} insufficient data`} />
        <StatTile label="Processing errors" value={o.processing_errors.length} tone={o.processing_errors.length ? "red" : "green"}
          sub="failed or invalid documents" />
        <StatTile label="Evidence coverage" value={ev.fields_with_source_page_pct === null ? "—" : `${ev.fields_with_source_page_pct}%`}
          tone={ev.fields_with_source_page_pct === 100 ? "green" : "amber"}
          sub={`${ev.fields_with_location_pct ?? "—"}% with exact location`} />
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Documents missing" className="lg:col-span-1"
          actions={<Link href={`/applications/${id}/upload`} className="text-xs text-indigo-700 hover:underline">Checklist →</Link>}>
          {missing.length === 0 && o.documents_missing.officer_to_confirm.length === 0 ? (
            <Empty>All required documents received.</Empty>
          ) : (
            <div className="space-y-2 text-sm">
              {missing.map((t) => <div key={t} className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-rose-500" />{humanize(t)}</div>)}
              {o.documents_missing.officer_to_confirm.map((t) => (
                <div key={t} className="flex items-center gap-2 text-violet-800"><span className="h-2 w-2 rounded-full bg-violet-500" />{humanize(t)} <span className="text-xs">(conditional — officer to confirm)</span></div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Processing errors" className="lg:col-span-2">
          {o.processing_errors.length === 0 ? <Empty>No failed or invalid documents.</Empty> : (
            <ul className="space-y-2 text-sm">
              {o.processing_errors.map((e) => (
                <li key={e.document_id}>
                  <Link href={`/documents/${e.document_id}`} className="font-medium text-indigo-700 hover:underline">{e.document_code} · {e.filename}</Link>
                  <span className="ml-2 text-xs font-semibold text-rose-700">{e.status}</span>
                  <div className="break-words text-xs text-rose-800">{e.error}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Cross-document inconsistencies"
        actions={<Link href={`/applications/${id}/validation`} className="text-xs text-indigo-700 hover:underline">All checks →</Link>}>
        {o.cross_document.items.length ? <ReconciliationList results={o.cross_document.items} /> : (
          <Empty>{o.cross_document.checks ? "No inconsistencies between documents." : "No checks run yet."}</Empty>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Extraction quality">
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-slate-500">Fields extracted</dt><dd className="tabular-nums">{q.fields_extracted} / {q.fields_total}</dd>
            <dt className="text-slate-500">Required fields missing</dt><dd className="tabular-nums">{q.required_fields_missing}</dd>
            <dt className="text-slate-500">Low-confidence fields</dt><dd className="tabular-nums">{q.low_confidence_fields}</dd>
            <dt className="text-slate-500">Table rows</dt><dd className="tabular-nums">{q.table_rows_total} ({q.transactions} transactions)</dd>
            <dt className="text-slate-500">Rows failed / needing review</dt><dd className="tabular-nums">{q.rows_failed} / {q.rows_needs_review}</dd>
            {Object.entries(q.fields_by_status).map(([k, v]) => (
              <Fragment key={k}>
                <dt className="text-slate-500">Fields {humanize(k).toLowerCase()}</dt>
                <dd className="tabular-nums">{v}</dd>
              </Fragment>
            ))}
          </dl>
        </Card>
        <Card title="Validation issues by rule">
          {Object.keys(o.validation_issues.by_rule).length === 0 ? <Empty>No validation issues.</Empty> : (
            <ul className="space-y-1 text-sm">
              {Object.entries(o.validation_issues.by_rule).sort((a, b) => b[1] - a[1]).map(([rule, n]) => (
                <li key={rule} className="flex justify-between"><span>{humanize(rule)}</span><span className="tabular-nums text-slate-600">{n}</span></li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card title="Documents">
        <DocumentsTable documents={o.documents} />
      </Card>
    </div>
  );
}
