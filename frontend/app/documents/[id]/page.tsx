"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Fragment, useMemo, useState } from "react";
import { api, originalFileUrl } from "@/lib/api";
import { ACTIVE_DOC_STATUSES, useApi } from "@/lib/hooks";
import { formatBytes, formatINR, humanize, pct } from "@/lib/format";
import { EvidenceViewer, type Highlight } from "@/components/EvidenceViewer";
import { PipelineList } from "@/components/PipelineProgress";
import { Badge, Button, Card, ConfidenceBar, Empty, ErrorBanner, Loading, StatusBadge, Table } from "@/components/ui";
import type { ExtractedField, ExtractedTable, Provenance, TableRow } from "@/types";

const DOC_TYPES = [
  "PAN", "KYC", "GST_CERTIFICATE", "GST_RETURN", "UDYAM", "BANK_STATEMENT", "ITR", "PROFIT_LOSS", "BALANCE_SHEET",
  "CASH_FLOW", "LOAN_STATEMENT", "BUSINESS_REGISTRATION", "PARTNERSHIP_DEED", "LLP_AGREEMENT", "MOA", "AOA",
  "QUOTATION", "BUSINESS_PLAN", "UNKNOWN",
];
type Tab = "fields" | "tables" | "validation" | "pages";

export default function DocumentPage() {
  const { id } = useParams<{ id: string }>();
  const status = useApi(() => api.documentStatus(id), [id], {
    shouldPoll: (s) => ACTIVE_DOC_STATUSES.has(s.document_status),
  });
  const version = `${status.data?.processing_run}-${status.data?.document_status}`;
  const doc = useApi(() => api.document(id), [id, version]);
  const ext = useApi(() => api.extraction(id), [id, version]);
  const vals = useApi(() => api.documentValidation(id), [id, version]);
  const pages = useApi(() => api.pages(id), [id, version]);

  const [tab, setTab] = useState<Tab>("fields");
  const [page, setPage] = useState(1);
  const [highlight, setHighlight] = useState<Highlight[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [prov, setProv] = useState<Provenance | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const selectField = async (f: ExtractedField) => {
    setSelected(f.id);
    setProv(null);
    if (f.source_page) setPage(f.source_page);
    const hs: Highlight[] = [];
    if (f.source_location?.bbox && f.source_page) hs.push({ page: f.source_page, bbox: f.source_location.bbox, label: f.field_name });
    f.source_location?.components?.forEach((c) => c.bbox && hs.push({ page: c.page, bbox: c.bbox, label: c.label }));
    setHighlight(hs);
    try {
      setProv(await api.provenance(f.id));
    } catch {
      /* provenance panel stays empty */
    }
  };
  const selectRow = (r: TableRow) => {
    setSelected(r.id);
    setProv(null);
    setPage(r.page_number);
    setHighlight(r.bbox ? [{ page: r.page_number, bbox: r.bbox, label: `row ${r.row_index}` }] : []);
  };

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      status.reload();
    }
  };

  const d = doc.data;
  const st = status.data;
  if (!d || !st) return status.error || doc.error ? <ErrorBanner message={status.error ?? doc.error} /> : <Loading />;
  const active = ACTIVE_DOC_STATUSES.has(st.document_status);
  const latestStages = st.latest_job?.stages ?? st.upload_validation?.stages ?? [];

  return (
    <div className="space-y-4">
      <div>
        <Link href={`/applications/${d.application_id}`} className="text-xs text-indigo-700 hover:underline">← Application</Link>
        <div className="mt-1 flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="font-mono text-xs text-slate-500">{d.document_code} · run #{d.processing_run} · {formatBytes(d.file_size)} · {d.page_count ?? "?"} page(s)</div>
            <h1 className="break-all text-lg font-semibold text-slate-900">{d.filename}</h1>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-sm">
              <StatusBadge status={st.document_status} />
              <span className="text-slate-600">{humanize(d.document_type)}</span>
              {d.classification_confidence !== null && (
                <span className="text-xs text-slate-500">({pct(d.classification_confidence)} via {d.classification_method})</span>
              )}
              {d.is_duplicate && <Badge tone="red">Duplicate</Badge>}
              {active && <span className="animate-pulse text-xs text-sky-700">processing…</span>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select className="rounded border border-slate-300 px-2 py-1.5 text-sm" value={d.document_type} disabled={busy || d.is_duplicate}
              onChange={(e) => act(() => api.overrideType(d.id, e.target.value))} title="Correct the document type (re-processes)">
              {DOC_TYPES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
            </select>
            <Button variant="secondary" disabled={busy || active || d.is_duplicate} onClick={() => act(() => api.process(d.id))}>Re-process</Button>
            <a href={originalFileUrl(d.id)} target="_blank" rel="noreferrer"
              className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50">
              Original file
            </a>
          </div>
        </div>
        {(st.error_message || st.status_reasons?.length) && (
          <div className={`mt-2 rounded-md border px-3 py-2 text-sm ${st.error_message ? "border-rose-200 bg-rose-50 text-rose-800" : "border-amber-200 bg-amber-50 text-amber-900"}`}>
            {st.error_message ?? <>Needs review: {st.status_reasons!.map(humanize).join(" · ")}</>}
          </div>
        )}
        <div className="mt-2"><ErrorBanner message={actionError} /></div>
      </div>

      <div className="grid gap-4 lg:grid-cols-12">
        <div className="space-y-4 lg:col-span-3 xl:col-span-3">
          <Card title="Pipeline">
            <PipelineList stages={st.pipeline} />
            {latestStages.some((s) => s.warnings?.length) && (
              <details className="mt-3 text-xs">
                <summary className="cursor-pointer text-slate-600">Stage warnings</summary>
                <ul className="mt-1 space-y-1">
                  {latestStages.flatMap((s) => (s.warnings ?? []).map((w, i) => (
                    <li key={`${s.stage}-${i}`} className="break-words text-amber-800"><span className="font-medium">{humanize(s.stage)}:</span> {w}</li>
                  )))}
                </ul>
              </details>
            )}
          </Card>
          <Card title="File validation">
            {d.file_validation ? (
              <ul className="space-y-1 text-xs">
                {d.file_validation.checks.map((c, i) => (
                  <li key={i} className="flex gap-2">
                    <span className={c.status === "PASS" ? "text-emerald-600" : c.status === "FAIL" ? "text-rose-600" : "text-amber-600"}>
                      {c.status === "PASS" ? "✓" : c.status === "FAIL" ? "✕" : "!"}
                    </span>
                    <span><span className="font-medium">{humanize(c.code)}</span> — {c.message}</span>
                  </li>
                ))}
              </ul>
            ) : <Empty>Not validated yet.</Empty>}
          </Card>
          {d.classification_evidence && (
            <Card title="Classification evidence">
              <ul className="space-y-1 text-xs">
                {d.classification_evidence.evidence.map((e, i) => (
                  <li key={i}><Badge>{e.signal}</Badge> <span className="text-slate-600">{e.detail}</span></li>
                ))}
              </ul>
              {d.classification_evidence.candidates.length > 1 && (
                <div className="mt-2 text-xs text-slate-500">
                  Other candidates: {d.classification_evidence.candidates.slice(1).map((c) => `${humanize(c.document_type)} (${c.score})`).join(", ")}
                </div>
              )}
              {d.classification_evidence.warnings.map((w, i) => <div key={i} className="mt-1 text-xs text-amber-800">{w}</div>)}
            </Card>
          )}
        </div>

        <div className="space-y-4 lg:col-span-5 xl:col-span-6">
          <div className="flex gap-1 border-b border-slate-200">
            {(["fields", "tables", "validation", "pages"] as Tab[]).map((t) => (
              <button key={t} onClick={() => setTab(t)}
                className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === t ? "border-indigo-700 font-medium text-indigo-800" : "border-transparent text-slate-600"}`}>
                {humanize(t)}
                {t === "validation" && vals.data?.length ? ` (${vals.data.length})` : ""}
                {t === "tables" && ext.data?.tables.length ? ` (${ext.data.tables.length})` : ""}
              </button>
            ))}
          </div>
          {!ext.data ? <Loading /> : tab === "fields" ? (
            <FieldsPanel fields={ext.data.fields} selected={selected} onSelect={selectField}
              summary={ext.data.summary} warnings={ext.data.warnings} />
          ) : tab === "tables" ? (
            <TablesPanel tables={ext.data.tables} selected={selected} onSelect={selectRow} />
          ) : tab === "validation" ? (
            <Card>
              {!vals.data?.length ? <Empty>No validation findings.</Empty> : (
                <ul className="divide-y divide-slate-100 text-sm">
                  {vals.data.map((v) => (
                    <li key={v.id} className="py-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusBadge status={v.status} />
                        <Badge tone={v.severity === "ERROR" ? "red" : v.severity === "WARNING" ? "amber" : "slate"}>{v.severity}</Badge>
                        <span className="text-xs font-semibold">{v.rule_code}</span>
                        <span className="text-xs text-slate-500">{humanize(v.scope)}{v.field_name ? ` · ${v.field_name}` : ""}{v.source_page ? ` · p.${v.source_page}` : ""}</span>
                      </div>
                      <div className="mt-0.5 text-xs text-slate-700">{v.message}</div>
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          ) : (
            <Card>
              {(pages.data ?? []).map((p) => (
                <details key={p.page_number} className="border-b border-slate-100 py-2" open={p.page_number === page}>
                  <summary className="cursor-pointer text-sm">
                    Page {p.page_number} · {humanize(p.text_source)} · {p.char_count} chars
                    {p.warnings?.map((w) => <span key={w.code} className="ml-2 text-xs text-amber-700">{w.code}</span>)}
                  </summary>
                  <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-[11px] text-slate-700">{p.raw_text || "(no text extracted)"}</pre>
                </details>
              ))}
            </Card>
          )}
        </div>

        <div className="lg:col-span-4 xl:col-span-3">
          <div className="space-y-3 lg:sticky lg:top-4">
            <Card title="Source evidence">
              <EvidenceViewer documentId={d.id} pages={pages.data ?? []} highlight={highlight} page={page} onPageChange={setPage} />
              {prov ? (
                <pre className="mt-3 whitespace-pre-wrap rounded bg-slate-900 p-2 text-[11px] leading-relaxed text-slate-100">{prov.explanation}</pre>
              ) : (
                <p className="mt-2 text-xs text-slate-500">Select a field or table row to see exactly where it came from.</p>
              )}
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

function FieldsPanel({ fields, selected, onSelect, summary, warnings }: {
  fields: ExtractedField[];
  selected: string | null;
  onSelect: (f: ExtractedField) => void;
  summary: { fields_extracted: number; fields_total: number; required_missing: string[] };
  warnings: string[];
}) {
  if (!fields.length) return <Card><Empty>No structured fields for this document type (raw text and tables are preserved).</Empty></Card>;
  return (
    <Card title={`Extracted fields (${summary.fields_extracted}/${summary.fields_total})`}
      actions={summary.required_missing.length > 0 && <Badge tone="red">{summary.required_missing.length} required missing</Badge>}>
      <Table head={["Field", "Value", "Confidence", "Page", "Method", "Validation"]}>
        {fields.map((f) => (
          <tr key={f.id} onClick={() => !f.is_missing && onSelect(f)}
            className={`align-top ${f.is_missing ? "bg-rose-50/40" : "cursor-pointer hover:bg-indigo-50"} ${selected === f.id ? "bg-indigo-50" : ""}`}>
            <td className="px-2 py-1.5 text-xs font-medium text-slate-700">
              {f.field_name}{f.is_required && <span className="text-rose-600" title="required">*</span>}
            </td>
            <td className="max-w-[14rem] px-2 py-1.5 text-xs">
              {f.is_missing ? (
                <span className={`italic ${f.extraction_status === "OCR_REQUIRED" ? "text-violet-700" : "text-slate-400"}`}>
                  {f.extraction_status && f.extraction_status !== "NOT_FOUND" ? humanize(f.extraction_status).toLowerCase() : "not found"}
                </span>
              ) : (
                <>
                  {f.extraction_status && f.extraction_status !== "EXTRACTED" && (
                    <div className="mb-0.5"><StatusBadge status={f.extraction_status} /></div>
                  )}
                  <div className="break-words font-medium text-slate-900">{f.value_type === "amount" ? formatINR(f.normalized_value) : f.normalized_value ?? <span className="text-rose-700">unparseable</span>}</div>
                  {f.raw_value !== f.normalized_value && <div className="break-words text-slate-500">raw: {f.raw_value}</div>}
                  {f.warnings?.filter((w) => !w.startsWith("NOT_FOUND")).map((w, i) => <div key={i} className="text-amber-700">{w}</div>)}
                </>
              )}
            </td>
            <td className="px-2 py-1.5">{f.is_missing ? "—" : <ConfidenceBar value={f.confidence} />}</td>
            <td className="px-2 py-1.5 text-xs tabular-nums">{f.source_page ?? "—"}</td>
            <td className="px-2 py-1.5 text-xs text-slate-500">{f.extraction_method}</td>
            <td className="px-2 py-1.5"><StatusBadge status={f.validation_status} /></td>
          </tr>
        ))}
      </Table>
      {warnings.length > 0 && (
        <details className="mt-3 text-xs">
          <summary className="cursor-pointer text-slate-600">All extraction warnings ({warnings.length})</summary>
          <ul className="mt-1 space-y-0.5 text-amber-800">{warnings.map((w, i) => <li key={i} className="break-words">{w}</li>)}</ul>
        </details>
      )}
    </Card>
  );
}

const TXN_COLS = ["date", "description", "reference", "debit", "credit", "balance"] as const;

function TablesPanel({ tables, selected, onSelect }: { tables: ExtractedTable[]; selected: string | null; onSelect: (r: TableRow) => void }) {
  const [onlyIssues, setOnlyIssues] = useState(false);
  const counts = useMemo(() => tables.flatMap((t) => t.rows).reduce<Record<string, number>>((a, r) => ({ ...a, [r.status]: (a[r.status] ?? 0) + 1 }), {}), [tables]);
  if (!tables.length) return <Card><Empty>No tables detected.</Empty></Card>;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        {Object.entries(counts).map(([k, v]) => <span key={k} className="flex items-center gap-1"><StatusBadge status={k} />{v}</span>)}
        <label className="ml-auto flex items-center gap-1"><input type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} /> only rows needing attention</label>
      </div>
      {tables.map((t) => {
        const isTxn = t.table_type === "TRANSACTIONS";
        const rows = t.rows.filter((r) => !onlyIssues || r.status === "FAILED" || r.status === "NEEDS_REVIEW");
        return (
          <Card key={t.id} title={`Table ${t.table_index} · ${humanize(t.table_type)} · page ${t.page_number}${t.page_end && t.page_end !== t.page_number ? `–${t.page_end}` : ""}`}
            actions={<span className="text-xs text-slate-500">{t.extraction_method} · {t.row_count} rows{t.rows_failed ? ` · ${t.rows_failed} failed` : ""}</span>}>
            {t.warnings?.map((w, i) => <div key={i} className="mb-1 text-xs text-amber-800">{w}</div>)}
            <div className="max-h-[28rem] overflow-auto">
              <Table head={["#", "Kind", ...(isTxn ? TXN_COLS.map(humanize) : ["Cells"]), "Status"]}>
                {rows.map((r) => (
                  <Fragment key={r.id}>
                    <tr onClick={() => onSelect(r)}
                      className={`cursor-pointer align-top text-xs hover:bg-indigo-50 ${selected === r.id ? "bg-indigo-50" : ""} ${r.status === "FAILED" ? "bg-rose-50" : r.status === "NEEDS_REVIEW" ? "bg-amber-50" : ""}`}>
                      <td className="px-2 py-1 tabular-nums text-slate-400">{r.row_index}</td>
                      <td className="px-2 py-1 text-slate-500">{humanize(r.row_kind)}</td>
                      {isTxn ? TXN_COLS.map((c) => (
                        <td key={c} className={`px-2 py-1 ${["debit", "credit", "balance"].includes(c) ? "text-right tabular-nums" : ""}`}>
                          {r.parsed?.[c] ?? (c === "description" && r.parsed?.text) ?? ""}
                        </td>
                      )) : <td className="px-2 py-1 text-slate-700">{r.raw_cells.map((c) => c ?? "").join(" | ")}</td>}
                      <td className="px-2 py-1"><StatusBadge status={r.status} /></td>
                    </tr>
                    {r.errors?.length ? (
                      <tr className="text-[11px]">
                        <td />
                        <td colSpan={isTxn ? 8 : 3} className="px-2 pb-1 text-amber-800">{r.errors.join(" · ")}</td>
                      </tr>
                    ) : null}
                  </Fragment>
                ))}
              </Table>
            </div>
          </Card>
        );
      })}
    </div>
  );
}
