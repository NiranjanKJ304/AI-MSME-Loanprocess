import Link from "next/link";
import type { Completeness, RequirementItem } from "@/types";
import { humanize } from "@/lib/format";
import { StatusBadge } from "@/components/ui";

const GROUPS: { key: RequirementItem["mandatory_status"]; title: string; hint: string }[] = [
  { key: "MANDATORY", title: "Required", hint: "Needed for every application of this type" },
  { key: "CONDITIONAL", title: "Conditional", hint: "Required when the stated condition applies" },
  { key: "SUPPORTING", title: "Supporting", hint: "Optional; strengthens the file" },
];

export function RequirementChecklist({ completeness }: { completeness: Completeness }) {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        {GROUPS.map((g) => {
          const items = completeness.items.filter((i) => i.mandatory_status === g.key);
          return (
            <div key={g.key}>
              <div className="mb-1.5">
                <div className="text-sm font-semibold text-slate-800">{g.title} <span className="font-normal text-slate-400">({items.length})</span></div>
                <div className="text-xs text-slate-500">{g.hint}</div>
              </div>
              <ul className="space-y-1.5">
                {items.map((i) => (
                  <li key={i.requirement_id} className="rounded border border-slate-200 bg-white px-2.5 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-slate-800">{humanize(i.document_type)}</span>
                      <StatusBadge status={i.state} />
                    </div>
                    <div className="mt-0.5 text-xs text-slate-500">{i.description}</div>
                    {i.condition && (
                      <div className="mt-0.5 text-xs text-violet-700">
                        Condition: {i.condition}
                        {i.condition_met === true && " — applies"}
                        {i.condition_met === false && " — does not apply"}
                        {i.condition_met === null && i.mandatory_status === "CONDITIONAL" && " — officer to confirm"}
                      </div>
                    )}
                    {i.documents.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {i.documents.map((d) => (
                          <Link key={d.id} href={`/documents/${d.id}`} className="text-xs text-indigo-700 hover:underline">
                            {d.document_code}
                          </Link>
                        ))}
                        {i.satisfied_via && <span className="text-xs text-slate-500">(via alternative document)</span>}
                      </div>
                    )}
                  </li>
                ))}
                {!items.length && <li className="text-xs text-slate-400">None for this applicant / loan type</li>}
              </ul>
            </div>
          );
        })}
      </div>
      <p className="text-xs italic text-slate-500">
        Policy: {completeness.policy.name} v{completeness.policy.version}. {completeness.policy.disclaimer}
      </p>
    </div>
  );
}
