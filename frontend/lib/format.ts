export function formatINR(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isNaN(n)) return String(value);
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(n);
}

export function pct(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

const ACRONYMS = new Set([
  "PAN", "GST", "GSTIN", "GSTR", "KYC", "MOA", "AOA", "ITR", "LLP", "OCR", "LLM", "EMI", "IFSC", "PL", "BS",
  "EBITDA", "PBT", "PAT", "FY", "AY", "NIC", "CIN", "ID", "MIME", "PDF",
]);

/** "CROSS_DOCUMENT:GSTIN_CONSISTENCY" -> "Cross Document · GSTIN Consistency" */
export function humanize(code: string | null | undefined): string {
  if (!code) return "—";
  return code
    .split(":")
    .map((part) =>
      part
        .split("_")
        .filter(Boolean)
        .map((w) => (ACRONYMS.has(w.toUpperCase()) ? w.toUpperCase() : w[0].toUpperCase() + w.slice(1).toLowerCase()))
        .join(" "),
    )
    .join(" · ");
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
