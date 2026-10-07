import Link from "next/link";
import type { ReconciliationResult } from "@/types";
import { humanize, pct } from "@/lib/format";
import { Empty, StatusBadge } from "@/components/ui";

export function ReconciliationList({ results }: { results: ReconciliationResult[] }) {
  if (!results.length) return <Empty>No cross-document checks yet — process documents first.</Empty>;
  return (
    <ul className="divide-y divide-slate-100">
      {results.map((r) => {
        const details = r.details as { variance?: number; threshold?: number; comparisons?: { a: string; b: string; similarity: number; b_value?: string }[]; notes?: string[] } | null;
        return (
          <li key={r.id} className="py-3">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={r.status} />
              <span className="text-sm font-semibold text-slate-800">{humanize(r.check_code)}</span>
              <span className="text-xs text-slate-400">{humanize(r.check_group)}</span>
              {r.confidence !== null && <span className="text-xs text-slate-500">confidence {pct(r.confidence)}</span>}
            </div>
            <p className="mt-1 text-sm text-slate-700">{r.message}</p>
            {details?.variance !== undefined && (
              <p className="text-xs text-slate-500">
                Variance {pct(details.variance, 1)} vs configured threshold {pct(details.threshold ?? null)}
                {details.notes?.length ? ` · ${details.notes.join(" · ")}` : ""}
              </p>
            )}
            {details?.comparisons && (
              <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                {details.comparisons.map((c, i) => (
                  <span key={i} className="rounded bg-slate-100 px-1.5 py-0.5">{c.a} ↔ {c.b}: {pct(c.similarity)}</span>
                ))}
              </div>
            )}
            {r.sources.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {r.sources.map((s, i) => (
                  <span key={i} className="rounded border border-slate-200 bg-white px-1.5 py-0.5 text-xs text-slate-600"
                    title={`${s.filename ?? ""} ${s.field}${s.page ? ` (page ${s.page})` : ""}`}>
                    {s.document_id ? (
                      <Link href={`/documents/${s.document_id}`} className="font-mono text-indigo-700 hover:underline">{s.document_code}</Link>
                    ) : <span className="font-mono">{s.document_code}</span>}
                    {" "}{s.field}: <span className="font-medium text-slate-800">{s.normalized_value ?? s.value ?? "—"}</span>
                    {s.page ? <span className="text-slate-400"> p.{s.page}</span> : null}
                  </span>
                ))}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
