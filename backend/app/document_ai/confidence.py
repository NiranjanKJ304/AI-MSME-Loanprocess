"""Extraction confidence model.

Confidence is a product of explainable factors (stored with every field for audit):

  position  where the value sat relative to its label  (beside > table cell > below > above > unlabelled)
  format    the value parsed as its expected type        (valid x1.05 / unformatted x0.92 / invalid x0.5)
  source    native text 1.0; OCR scaled by the OCR confidence of the value's own words
  repair    OCR character correction applied              (x0.85)
  label     less specific label synonym                   (x0.98 per step, floor 0.9)

Ambiguity (several distinct candidates of similar strength) is applied when candidates are
compared (extractor.choose). The result is capped below 1.0 - nothing is "certain".
"""

from __future__ import annotations

from typing import Any

from app.document_ai.types import ParsedPage
from app.models.enums import TextSource

CAP = 0.97
POSITION_BASE = {
    "RIGHT": 0.92,  # value beside its label on the same line
    "INLINE": 0.92,  # label and value in one cell/segment ("GSTIN: 27ABC...")
    "TABLE_RIGHT": 0.90,  # label cell -> neighbouring cell
    "TABLE_ROW": 0.90,  # financial line item row
    "BELOW": 0.86,  # value under its label
    "TABLE_BELOW": 0.85,  # header cell -> cell below
    "ABOVE": 0.72,  # caption under the value (weak)
    "PATTERN": 0.70,  # identifier pattern with no label
    "DERIVED": 0.90,
    "LLM": 0.60,
}
AMBIGUITY_FACTOR = 0.7


def source_factor(page: ParsedPage | None, word_conf: float | None) -> tuple[float, str]:
    if page is None:
        return 1.0, "UNKNOWN"
    src = page.text_source
    if src in (TextSource.TEXT_LAYER, TextSource.SPREADSHEET):
        return 1.0, src.value
    if src == TextSource.NONE:
        return 0.0, src.value
    # OCR / HYBRID: native words in a hybrid page carry no OCR confidence
    if word_conf is None:
        if src == TextSource.HYBRID:
            return 1.0, "HYBRID_NATIVE"
        word_conf = page.ocr_confidence or 0.0
    return round(0.55 + 0.45 * min(100.0, word_conf) / 100.0, 3), f"{src.value}({word_conf:.0f}%)"


def score(
    position: str,
    *,
    page: ParsedPage | None,
    word_conf: float | None = None,
    type_ok: bool | None = None,
    unformatted: bool = False,
    repaired: bool = False,
    label_priority: int = 0,
    base_override: float | None = None,
) -> tuple[float, dict[str, Any]]:
    base = base_override if base_override is not None else POSITION_BASE.get(position, 0.8)
    factors: dict[str, Any] = {"position": position, "base": base}
    conf = base
    if type_ok is False:
        conf *= 0.5
        factors["format"] = 0.5
    elif type_ok:
        f = 0.92 if unformatted else 1.05
        conf *= f
        factors["format"] = f
    src_f, src_label = source_factor(page, word_conf)
    conf *= src_f
    factors["source"] = src_f
    factors["text_source"] = src_label
    if repaired:
        conf *= 0.85
        factors["ocr_repair"] = 0.85
    if label_priority:
        lf = max(0.9, 1 - 0.02 * label_priority)
        conf *= lf
        factors["label_specificity"] = round(lf, 3)
    conf = round(min(CAP, conf), 3)
    factors["final"] = conf
    return conf, factors
