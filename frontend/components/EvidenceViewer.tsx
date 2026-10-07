"use client";

import { useState } from "react";
import { pageImageUrl } from "@/lib/api";
import type { PageInfo } from "@/types";

export interface Highlight {
  page: number;
  bbox: number[]; // [x0, y0, x1, y1] in page coordinates
  label?: string;
}

/**
 * Renders the original page and outlines the exact region a value was extracted from.
 * Boxes are positioned in percentages of page width/height, so no pixel measuring is needed.
 */
export function EvidenceViewer({ documentId, pages, highlight, onPageChange, page }: {
  documentId: string;
  pages: PageInfo[];
  highlight: Highlight[];
  page: number;
  onPageChange: (p: number) => void;
}) {
  const [failed, setFailed] = useState<Record<number, boolean>>({});
  const info = pages.find((p) => p.page_number === page);
  const boxes = highlight.filter((h) => h.page === page && h.bbox?.length === 4);

  if (!pages.length) return <div className="text-sm text-slate-500">No pages extracted.</div>;
  return (
    <div>
      <div className="mb-2 flex items-center justify-between text-xs text-slate-600">
        <div className="flex items-center gap-1">
          <button className="rounded border px-2 py-0.5 disabled:opacity-40" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>‹</button>
          <span className="tabular-nums">Page {page} / {pages.length}</span>
          <button className="rounded border px-2 py-0.5 disabled:opacity-40" disabled={page >= pages.length} onClick={() => onPageChange(page + 1)}>›</button>
        </div>
        {info && (
          <span>
            {info.text_source === "OCR" ? `OCR (${info.ocr_confidence ?? "?"}%)` : info.text_source === "NONE" ? "No text extracted" : "Text layer"}
          </span>
        )}
      </div>
      <div className="relative w-full overflow-hidden rounded border border-slate-200 bg-slate-50">
        {failed[page] ? (
          <div className="p-6 text-center text-sm text-slate-500">Page image unavailable for this file type.</div>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={pageImageUrl(documentId, page)} alt={`Page ${page}`} className="block w-full"
            onError={() => setFailed((f) => ({ ...f, [page]: true }))} />
        )}
        {!failed[page] && info?.width && info?.height && boxes.map((h, i) => {
          const [x0, y0, x1, y1] = h.bbox;
          return (
            <div key={i} title={h.label}
              className="pointer-events-none absolute rounded-sm border-2 border-rose-500 bg-rose-400/20"
              style={{
                left: `${(x0 / info.width!) * 100}%`,
                top: `${(y0 / info.height!) * 100}%`,
                width: `${((x1 - x0) / info.width!) * 100}%`,
                height: `${((y1 - y0) / info.height!) * 100}%`,
              }} />
          );
        })}
      </div>
    </div>
  );
}
