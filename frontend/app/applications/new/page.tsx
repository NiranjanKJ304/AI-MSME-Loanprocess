"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { humanize } from "@/lib/format";
import { Button, Card, ErrorBanner } from "@/components/ui";

const APPLICANT_TYPES = ["SOLE_PROPRIETOR", "PARTNERSHIP", "LLP", "PRIVATE_LIMITED", "PUBLIC_LIMITED", "OTHER"];
const LOAN_TYPES = ["TERM_LOAN", "WORKING_CAPITAL", "PERSONAL_BUSINESS_LOAN", "MACHINERY_LOAN", "OTHER"];

const input = "mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500";

export default function NewApplicationPage() {
  const router = useRouter();
  const [form, setForm] = useState({
    business_name: "",
    applicant_type: "SOLE_PROPRIETOR",
    loan_type: "TERM_LOAN",
    requested_amount: "",
    loan_purpose: "",
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
    setForm({ ...form, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const app = await api.createApplication({ ...form, loan_purpose: form.loan_purpose || null });
      router.push(`/applications/${app.id}/upload`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-xl font-semibold text-slate-900">New MSME loan application</h1>
      <Card>
        <form onSubmit={submit} className="space-y-4">
          <label className="block text-sm font-medium text-slate-700">
            Business name
            <input required minLength={2} className={input} value={form.business_name} onChange={set("business_name")}
              placeholder="As registered (e.g. on GST / incorporation certificate)" />
          </label>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm font-medium text-slate-700">
              Applicant type
              <select className={input} value={form.applicant_type} onChange={set("applicant_type")}>
                {APPLICANT_TYPES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
              </select>
            </label>
            <label className="block text-sm font-medium text-slate-700">
              Loan type
              <select className={input} value={form.loan_type} onChange={set("loan_type")}>
                {LOAN_TYPES.map((t) => <option key={t} value={t}>{humanize(t)}</option>)}
              </select>
            </label>
          </div>
          <label className="block text-sm font-medium text-slate-700">
            Requested amount (INR)
            <input required type="number" min="1" step="0.01" className={input} value={form.requested_amount}
              onChange={set("requested_amount")} placeholder="2500000" />
          </label>
          <label className="block text-sm font-medium text-slate-700">
            Loan purpose
            <textarea rows={3} className={input} value={form.loan_purpose} onChange={set("loan_purpose")}
              placeholder="e.g. Purchase of CNC machine; working capital for raw material" />
          </label>
          <ErrorBanner message={error} />
          <div className="flex justify-end">
            <Button type="submit" disabled={busy}>{busy ? "Creating…" : "Create and upload documents"}</Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
