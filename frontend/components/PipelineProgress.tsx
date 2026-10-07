import type { PipelineStage } from "@/types";
import { humanize } from "@/lib/format";

const ICON: Record<string, { icon: string; cls: string }> = {
  COMPLETED: { icon: "✓", cls: "bg-emerald-600 text-white" },
  COMPLETED_WITH_WARNINGS: { icon: "!", cls: "bg-amber-500 text-white" },
  FAILED: { icon: "✕", cls: "bg-rose-600 text-white" },
  RUNNING: { icon: "⏳", cls: "bg-sky-100 text-sky-700 animate-pulse" },
  SKIPPED: { icon: "–", cls: "bg-slate-200 text-slate-500" },
  PENDING: { icon: "○", cls: "bg-slate-100 text-slate-400" },
};

/** Compact horizontal pipeline (used in document lists). */
export function PipelineStrip({ stages }: { stages?: PipelineStage[] }) {
  if (!stages) return null;
  return (
    <div className="flex items-center gap-0.5">
      {stages.map((s) => {
        const ic = ICON[s.status] ?? ICON.PENDING;
        return (
          <span key={s.stage}
            title={`${humanize(s.stage)}: ${humanize(s.status)}${s.error ? ` — ${s.error}` : ""}${s.warnings ? ` (${s.warnings} warning(s))` : ""}`}
            className={`flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold ${ic.cls}`}>
            {ic.icon}
          </span>
        );
      })}
    </div>
  );
}

/** Full vertical pipeline with counts, warnings and errors (document detail / processing view). */
export function PipelineList({ stages }: { stages: PipelineStage[] }) {
  return (
    <ol className="space-y-1.5">
      {stages.map((s) => {
        const ic = ICON[s.status] ?? ICON.PENDING;
        return (
          <li key={s.stage} className="flex items-start gap-2">
            <span className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${ic.cls}`}>
              {ic.icon}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
                <span className="font-medium text-slate-800">{humanize(s.stage)}</span>
                <span className="text-xs text-slate-500">{humanize(s.status)}</span>
                {s.status !== "PENDING" && s.status !== "SKIPPED" && (
                  <span className="text-xs tabular-nums text-slate-500">
                    {s.records_processed} processed{s.records_failed ? `, ${s.records_failed} failed` : ""}
                    {s.warnings ? `, ${s.warnings} warning(s)` : ""}
                  </span>
                )}
              </div>
              {s.error && <div className="mt-0.5 break-words text-xs text-rose-700">{s.error}</div>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
