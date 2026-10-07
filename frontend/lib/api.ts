import type {
  Application,
  ApplicationListItem,
  AuditEntry,
  Completeness,
  DocumentDetail,
  DocumentStatusResponse,
  DocumentSummary,
  Extraction,
  Job,
  Meta,
  Overview,
  PageInfo,
  Provenance,
  ReconciliationResult,
  ValidationResult,
} from "@/types";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(0, `Cannot reach the backend at ${API_BASE}. Is it running?`);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  meta: () => request<Meta>("/api/meta"),
  listApplications: () => request<ApplicationListItem[]>("/api/applications"),
  createApplication: (payload: {
    business_name: string;
    applicant_type: string;
    loan_type: string;
    requested_amount: string;
    loan_purpose: string | null;
  }) => request<Application>("/api/applications", { method: "POST", ...json(payload) }),
  getApplication: (id: string) => request<Application>(`/api/applications/${id}`),
  overview: (id: string) => request<Overview>(`/api/applications/${id}/overview`),
  documents: (id: string) => request<DocumentSummary[]>(`/api/applications/${id}/documents`),
  completeness: (id: string) => request<Completeness>(`/api/applications/${id}/completeness`),
  validation: (id: string) =>
    request<{
      summary: { total: number; by_severity: Record<string, number>; by_status: Record<string, number> };
      file_issues: { document_id: string; document_code: string; filename: string; status: string; error: string | null }[];
      results: ValidationResult[];
    }>(`/api/applications/${id}/validation`),
  reconciliation: (id: string) =>
    request<{ summary: Record<string, number>; note: string; results: ReconciliationResult[] }>(
      `/api/applications/${id}/reconciliation`,
    ),
  audit: (id: string) => request<AuditEntry[]>(`/api/applications/${id}/audit`),
  reconcile: (id: string) => request<Job>(`/api/applications/${id}/reconcile`, { method: "POST" }),
  processAll: (id: string) => request<Job[]>(`/api/applications/${id}/process`, { method: "POST" }),
  upload: (id: string, files: File[], autoProcess = true) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f, f.name));
    return request<{ documents: DocumentSummary[]; accepted: number; rejected: number; processing_scheduled: boolean }>(
      `/api/applications/${id}/documents?auto_process=${autoProcess}`,
      { method: "POST", body: form },
    );
  },
  document: (id: string) => request<DocumentDetail>(`/api/documents/${id}`),
  documentStatus: (id: string) => request<DocumentStatusResponse>(`/api/documents/${id}/status`),
  extraction: (id: string) => request<Extraction>(`/api/documents/${id}/extraction`),
  documentValidation: (id: string) => request<ValidationResult[]>(`/api/documents/${id}/validation`),
  pages: (id: string) => request<PageInfo[]>(`/api/documents/${id}/pages`),
  process: (id: string) => request<Job>(`/api/documents/${id}/process?background=true`, { method: "POST" }),
  overrideType: (id: string, document_type: string) =>
    request<DocumentDetail>(`/api/documents/${id}/type`, { method: "PATCH", ...json({ document_type, reprocess: true }) }),
  provenance: (fieldId: string) => request<Provenance>(`/api/fields/${fieldId}/provenance`),
};

export const pageImageUrl = (documentId: string, page: number, zoom = 1.5) =>
  `${API_BASE}/api/documents/${documentId}/pages/${page}/image?zoom=${zoom}`;
export const originalFileUrl = (documentId: string) => `${API_BASE}/api/documents/${documentId}/file`;
