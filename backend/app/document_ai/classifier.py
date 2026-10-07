"""Layered document classifier.

Signals (each contributes a weighted score per document type):
  1. filename hints          - weak (0.35 max); a filename alone can never reach the threshold
  2. text patterns            - identifiers (GSTIN, PAN, Udyam no., IFSC ...)
  3. document keywords        - titles / form names / characteristic labels
  4. layout indicators        - e.g. a transaction table => bank statement
  5. LLM fallback             - only if confidence is below threshold and a provider is configured

confidence = strength * (0.6 + 0.4 * margin), where strength saturates at STRONG_SCORE and
margin is the relative gap to the runner-up type - ambiguous documents get low confidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.config import get_settings
from app.document_ai.table_extractor import analyze_table
from app.document_ai.types import ParsedDocument, RawTable
from app.llm import LLMResponseError, LLMUnavailable, get_llm_provider
from app.models.enums import ClassificationMethod, DocumentType, TableType
from app.utils.logging import get_logger

log = get_logger("classifier")

STRONG_SCORE = 2.0
FILENAME_WEIGHT = 0.35

DT = DocumentType

# (pattern, weight). Patterns are applied case-insensitively to the document text.
TEXT_SIGNALS: dict[DocumentType, list[tuple[str, float]]] = {
    DT.PAN: [
        (r"permanent\s+account\s+number", 1.2),
        (r"income\s+tax\s+department", 0.6),
        (r"\bPAN\s*card\b", 0.4),
    ],
    DT.KYC: [
        (r"\baadhaar\b|unique\s+identification\s+authority", 1.2),
        (r"election\s+commission|voter", 0.9),
        (r"\bpassport\b", 0.6),
        (r"driving\s+licen[cs]e", 0.9),
    ],
    DT.GST_CERTIFICATE: [
        (r"form\s+gst\s+reg[-\s]?06", 1.5),
        (r"registration\s+certificate", 0.6),
        (r"constitution\s+of\s+business", 0.5),
        (r"date\s+of\s+liability", 0.4),
        (r"address\s+of\s+principal\s+place", 0.4),
        (r"goods\s+and\s+services\s+tax", 0.3),
    ],
    DT.GST_RETURN: [
        (r"\bGSTR[-\s]?(1|3B|9|9C|4)\b", 1.4),
        (r"tax\s+period", 0.4),
        (r"outward\s+(taxable\s+)?supplies", 0.6),
        (r"input\s+tax\s+credit|\bITC\b", 0.3),
    ],
    DT.UDYAM: [
        (r"UDYAM-[A-Z]{2}-\d{2}-\d{7}", 1.5),
        (r"udyam\s+registration", 0.8),
        (r"ministry\s+of\s+micro,?\s+small", 0.6),
        (r"type\s+of\s+enterprise", 0.4),
        (r"major\s+activity", 0.3),
    ],
    DT.BANK_STATEMENT: [
        (r"statement\s+of\s+account|account\s+statement|bank\s+statement", 1.0),
        (r"\bIFSC\b", 0.3),
        (r"opening\s+balance", 0.4),
        (r"closing\s+balance", 0.3),
        (r"withdrawal|debit", 0.2),
        (r"deposit|credit", 0.1),
        (r"narration|particulars", 0.2),
    ],
    DT.ITR: [
        (r"indian\s+income\s+tax\s+return", 1.4),
        (r"\bITR[-\s]?[1-7]\b", 0.8),
        (r"assessment\s+year", 0.6),
        (r"gross\s+total\s+income", 0.5),
        (r"acknowledg(e)?ment", 0.3),
    ],
    DT.PROFIT_LOSS: [
        (r"profit\s*(&|and)\s*loss|statement\s+of\s+profit", 1.4),
        (r"revenue\s+from\s+operations", 0.4),
        (r"profit\s+before\s+tax", 0.4),
        (r"cost\s+of\s+(goods\s+sold|materials)", 0.3),
        (r"finance\s+costs?", 0.2),
    ],
    DT.BALANCE_SHEET: [
        (r"balance\s+sheet", 1.4),
        (r"equity\s+and\s+liabilities|capital\s+and\s+liabilities", 0.5),
        (r"total\s+assets", 0.4),
        (r"trade\s+(payables|receivables)|sundry\s+(creditors|debtors)", 0.3),
        (r"reserves\s+and\s+surplus", 0.3),
    ],
    DT.CASH_FLOW: [
        (r"cash\s+flow\s+statement|statement\s+of\s+cash\s+flows?", 1.5),
        (r"operating\s+activities", 0.5),
        (r"investing\s+activities", 0.3),
    ],
    DT.LOAN_STATEMENT: [
        (r"loan\s+account\s+statement|loan\s+statement", 1.4),
        (r"repayment\s+schedule|amorti[sz]ation\s+schedule", 0.6),
        (r"\bEMI\b", 0.4),
        (r"principal\s+outstanding|outstanding\s+principal", 0.5),
    ],
    DT.BUSINESS_REGISTRATION: [
        (r"certificate\s+of\s+incorporation", 1.4),
        (r"shops?\s+(and|&)\s+establishments?", 1.2),
        (r"trade\s+licen[cs]e", 1.0),
        (r"registrar\s+of\s+(companies|firms)", 0.6),
        (r"corporate\s+identity\s+number|\bCIN\b", 0.4),
    ],
    DT.PARTNERSHIP_DEED: [
        (r"deed\s+of\s+partnership|partnership\s+deed", 1.6),
        (r"indian\s+partnership\s+act", 0.6),
    ],
    DT.LLP_AGREEMENT: [
        (r"limited\s+liability\s+partnership\s+agreement|LLP\s+agreement", 1.6),
        (r"designated\s+partners?", 0.4),
    ],
    DT.MOA: [(r"memorandum\s+of\s+association", 1.6), (r"objects?\s+clause", 0.3)],
    DT.AOA: [(r"articles\s+of\s+association", 1.6)],
    DT.QUOTATION: [
        (r"\bquotation\b|proforma\s+invoice|\bestimate\b", 1.3),
        (r"valid\s+(till|until|for)", 0.3),
        (r"terms\s+(and|&)\s+conditions", 0.1),
    ],
    DT.BUSINESS_PLAN: [
        (r"business\s+plan|project\s+report|detailed\s+project\s+report", 1.4),
        (r"executive\s+summary", 0.4),
        (r"means\s+of\s+finance|cost\s+of\s+project", 0.5),
    ],
}

# Identifier patterns are case-sensitive.
ID_SIGNALS: dict[DocumentType, list[tuple[str, float]]] = {
    DT.PAN: [(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", 0.3)],
    DT.GST_CERTIFICATE: [(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", 0.4)],
    DT.GST_RETURN: [(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", 0.3)],
    DT.BANK_STATEMENT: [(r"\b[A-Z]{4}0[A-Z0-9]{6}\b", 0.3)],
    DT.BUSINESS_REGISTRATION: [(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b", 0.6)],
}

FILENAME_HINTS: dict[DocumentType, str] = {
    DT.PAN: r"\bpan\b|pan[_\- ]?card",
    DT.KYC: r"aadhaar|aadhar|kyc|voter|passport|driving",
    DT.GST_CERTIFICATE: r"gst[_\- ]?(cert|reg)|reg[_\- ]?06",
    DT.GST_RETURN: r"gstr|gst[_\- ]?return",
    DT.UDYAM: r"udyam|msme[_\- ]?cert|udyog",
    DT.BANK_STATEMENT: r"bank|statement|stmt|passbook",
    DT.ITR: r"\bitr\b|itr[_\- ]?\d|income[_\- ]?tax[_\- ]?return|acknowledg",
    DT.PROFIT_LOSS: r"p\s?&\s?l|p_?and_?l|profit|pnl|p&l",
    DT.BALANCE_SHEET: r"balance[_\- ]?sheet|\bbs\b",
    DT.CASH_FLOW: r"cash[_\- ]?flow",
    DT.LOAN_STATEMENT: r"loan[_\- ]?(statement|account|schedule)|emi",
    DT.BUSINESS_REGISTRATION: r"incorporation|shop[_\- ]?act|trade[_\- ]?licen|registration",
    DT.PARTNERSHIP_DEED: r"partnership[_\- ]?deed|deed",
    DT.LLP_AGREEMENT: r"llp[_\- ]?agreement",
    DT.MOA: r"\bmoa\b|memorandum",
    DT.AOA: r"\baoa\b|articles",
    DT.QUOTATION: r"quot|proforma|estimate",
    DT.BUSINESS_PLAN: r"business[_\- ]?plan|project[_\- ]?report|\bdpr\b",
}


@dataclass
class ClassificationResult:
    document_type: DocumentType
    confidence: float
    method: ClassificationMethod
    evidence: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    needs_review: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_type": self.document_type.value,
            "confidence": self.confidence,
            "method": self.method.value,
            "evidence": self.evidence,
            "candidates": self.candidates,
            "warnings": self.warnings,
            "needs_review": self.needs_review,
        }


class _LLMClassification(BaseModel):
    document_type: DocumentType
    confidence: float = Field(ge=0, le=1)
    evidence_quote: str | None = Field(
        description="A short verbatim quote from the document supporting the classification"
    )


class DocumentClassifier:
    def __init__(self, threshold: float | None = None):
        self.threshold = threshold if threshold is not None else get_settings().classification_confidence_threshold

    def classify(
        self, filename: str, parsed: ParsedDocument | None, tables: list[RawTable] | None = None
    ) -> ClassificationResult:
        scores: dict[DocumentType, float] = {t: 0.0 for t in DocumentType}
        source: dict[DocumentType, dict[str, float]] = {t: {} for t in DocumentType}
        evidence: dict[DocumentType, list[dict[str, Any]]] = {t: [] for t in DocumentType}

        def add(t: DocumentType, kind: str, weight: float, detail: str) -> None:
            scores[t] += weight
            source[t][kind] = source[t].get(kind, 0.0) + weight
            evidence[t].append({"signal": kind, "weight": weight, "detail": detail})

        # 1. filename hints (weak by design)
        fname = filename.lower()
        for t, pat in FILENAME_HINTS.items():
            if re.search(pat, fname):
                add(t, "filename", FILENAME_WEIGHT, f"filename matches /{pat}/")

        text = parsed.lines_text() if parsed else ""
        if parsed and not text.strip():
            text = parsed.full_text
        # 2/3. text patterns + keywords
        for t, signals in TEXT_SIGNALS.items():
            for pat, w in signals:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    add(t, "keyword", w, f"'{m.group(0)[:60]}'")
        for t, signals in ID_SIGNALS.items():
            for pat, w in signals:
                m = re.search(pat, text)
                if m:
                    add(t, "identifier", w, f"identifier pattern '{m.group(0)}'")

        # 4. layout indicators
        if tables:
            txn = [tb for tb in tables if analyze_table(tb).table_type == TableType.TRANSACTIONS]
            if txn:
                rows = sum(len(tb.rows) for tb in txn)
                add(DT.BANK_STATEMENT, "layout", 0.8, f"transaction table(s) detected ({rows} rows)")

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        (top_t, top_s), (second_t, second_s) = ranked[0], ranked[1]
        candidates = [
            {"document_type": t.value, "score": round(s, 3)} for t, s in ranked[:4] if s > 0
        ]
        warnings: list[str] = []

        if top_s <= 0:
            result = ClassificationResult(
                DT.UNKNOWN, 0.0, ClassificationMethod.NONE, [], candidates, ["NO_CLASSIFICATION_SIGNALS"]
            )
        else:
            strength = min(1.0, top_s / STRONG_SCORE)
            margin = (top_s - second_s) / top_s
            confidence = round(strength * (0.6 + 0.4 * margin), 3)
            kinds = source[top_t]
            dominant = max(kinds.items(), key=lambda kv: kv[1])[0]
            method = {
                "filename": ClassificationMethod.FILENAME,
                "layout": ClassificationMethod.LAYOUT,
            }.get(dominant, ClassificationMethod.RULE)
            if set(kinds) == {"filename"}:
                warnings.append("FILENAME_ONLY_EVIDENCE: document content did not confirm the type")
            if second_s >= 0.8 * top_s and second_s >= 1.0:
                warnings.append(
                    f"AMBIGUOUS_TYPE: also resembles {second_t.value} (score {second_s:.2f} vs {top_s:.2f})"
                )
            result = ClassificationResult(
                top_t, confidence, method, evidence[top_t], candidates, warnings
            )

        if result.confidence < self.threshold:
            result = self._llm_fallback(result, text)
        result.needs_review = result.confidence < self.threshold or result.document_type == DT.UNKNOWN
        if result.needs_review:
            result.warnings.append(
                f"LOW_CLASSIFICATION_CONFIDENCE: {result.confidence:.2f} < {self.threshold:.2f}"
            )
        return result

    def _llm_fallback(self, rule_result: ClassificationResult, text: str) -> ClassificationResult:
        provider = get_llm_provider()
        if not provider.available() or not text.strip():
            return rule_result
        excerpt = text[: min(4000, get_settings().llm_max_input_chars)]
        system = (
            "You classify Indian MSME loan documents. Choose exactly one document_type from the "
            "enum. If the text does not clearly identify the document, answer UNKNOWN with low "
            "confidence. Quote verbatim evidence from the text; never invent text."
        )
        try:
            out = provider.complete_json(system, f"Document text:\n---\n{excerpt}\n---", _LLMClassification)
        except (LLMUnavailable, LLMResponseError) as exc:
            rule_result.warnings.append(f"LLM_CLASSIFIER_FAILED: {exc}")
            return rule_result
        quote_ok = bool(out.evidence_quote) and _normalized(out.evidence_quote) in _normalized(text)
        conf = min(out.confidence, 0.85)
        if not quote_ok:
            conf = min(conf, 0.5)
        if rule_result.document_type not in (out.document_type, DT.UNKNOWN) and rule_result.confidence > 0.2:
            conf = min(conf, 0.55)  # rules and LLM disagree: keep it in review
        if conf <= rule_result.confidence:
            rule_result.warnings.append(
                f"LLM_CLASSIFIER_SUGGESTED: {out.document_type.value} ({out.confidence:.2f}) - not adopted"
            )
            return rule_result
        evidence = [{"signal": "llm", "weight": conf, "detail": out.evidence_quote, "quote_verified": quote_ok}]
        return ClassificationResult(
            out.document_type,
            round(conf, 3),
            ClassificationMethod.LLM,
            evidence + rule_result.evidence,
            rule_result.candidates,
            rule_result.warnings,
        )


def _normalized(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()
