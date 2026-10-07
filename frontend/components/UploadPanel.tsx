"use client";

import { useRef, useState } from "react";
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { Button, ErrorBanner } from "@/components/ui";

export function UploadPanel({ applicationId, onUploaded }: { applicationId: string; onUploaded: () => void }) {
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const add = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list)]);
    setResult(null);
  };

  const submit = async () => {
    if (!files.length) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.upload(applicationId, files, true);
      setResult(
        `${res.documents.length} file(s) registered: ${res.accepted} accepted for processing, ${res.rejected} rejected (see reasons below).`,
      );
      setFiles([]);
      onUploaded();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}
        onClick={() => input.current?.click()}
        className={`cursor-pointer rounded-lg border-2 border-dashed px-4 py-8 text-center text-sm transition ${
          drag ? "border-indigo-500 bg-indigo-50" : "border-slate-300 bg-slate-50 hover:bg-slate-100"}`}>
        <div className="font-medium text-slate-700">Drop documents here or click to choose</div>
        <div className="mt-1 text-xs text-slate-500">PDF, PNG, JPG, TIFF, XLSX, CSV — multiple files allowed. Document types are detected automatically.</div>
        <input ref={input} type="file" multiple className="hidden" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.xlsx,.csv"
          onChange={(e) => { add(e.target.files); e.target.value = ""; }} />
      </div>
      {files.length > 0 && (
        <ul className="divide-y divide-slate-100 rounded border border-slate-200 text-sm">
          {files.map((f, i) => (
            <li key={`${f.name}-${i}`} className="flex items-center justify-between px-3 py-1.5">
              <span className="truncate">{f.name}</span>
              <span className="flex items-center gap-3 text-xs text-slate-500">
                {formatBytes(f.size)}
                <button className="text-rose-600 hover:underline" onClick={() => setFiles(files.filter((_, j) => j !== i))}>remove</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-center gap-3">
        <Button onClick={submit} disabled={!files.length || busy}>
          {busy ? "Uploading…" : `Upload & process ${files.length ? `(${files.length})` : ""}`}
        </Button>
        {result && <span className="text-sm text-slate-600">{result}</span>}
      </div>
      <ErrorBanner message={error} />
    </div>
  );
}
