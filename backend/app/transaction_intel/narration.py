"""Narration structure: channel, references, UPI handle and counterparty.

Indian narrations look like
    NEFT CR SHAKTHI PUMPS LTD            NEFT-HDFC0001234-SHAKTHI PUMPS LTD-N261973069
    UPI/DR/412345678901/ELECTRICITY BOARD/SBIN/tneb@sbi/Payment
    IMPS-P2A-412345-RAMESH K             ACH DR HDFC LOAN EMI 778
The counterparty is what remains after removing channel words, direction words, references,
IFSCs, VPAs, dates/months and generic words. raw_counterparty is the exact substring as printed;
normalized_counterparty is upper-cased with legal suffixes canonicalised. If nothing remains the
counterparty is None - it is never invented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.document_ai import normalizers as N
from app.models.enums import CounterpartyType
from app.transaction_intel.config import RuleSet

_SEP = re.compile(r"[/\-:|_*,;()]+")
_TOKEN = re.compile(r"[A-Za-z0-9@.&']+")
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")



def normalize_narration(text: str | None) -> str:
    return re.sub(r"\s+", " ", _SEP.sub(" ", (text or "").upper())).strip()


def _is_reference(tok: str) -> bool:
    t = tok.upper()
    digits = sum(c.isdigit() for c in t)
    if _IFSC.match(t):
        return True
    if t.isdigit():
        return len(t) >= 3  # cheque numbers, UTRs, batch ids, years
    return digits >= 4 and len(t) >= 6  # N261973069, HDFCR52024..., UTR strings


@dataclass
class NarrationInfo:
    normalized: str
    channel: str | None
    references: list[str] = field(default_factory=list)
    vpa: str | None = None
    raw_counterparty: str | None = None
    normalized_counterparty: str | None = None
    counterparty_type: CounterpartyType = CounterpartyType.UNKNOWN
    account_fragments: list[str] = field(default_factory=list)  # e.g. XXXX7788 / long digit strings


def parse_narration(text: str | None, rules: RuleSet, keyword_tokens: set[str] | None = None) -> NarrationInfo:
    """keyword_tokens: words that matched a classification rule ('EMI', 'DISBURSEMENT', 'CPIN'...);
    they describe the transaction type, not who the counterparty is, so they are not counterparty text."""
    raw = text or ""
    norm = normalize_narration(raw)
    cp = rules.counterparty
    channel_words = {w: ch for ch, words in rules.channels.items() for w in words}
    stop = set(cp.stopwords) | set(cp.month_tokens) | (keyword_tokens or set())

    info = NarrationInfo(normalized=norm, channel=None)
    mentions_account = bool(re.search(r"\b(?:A C|AC|ACCOUNT|ACCT|A C NO)\b", norm))
    kept: list[tuple[str, int, int]] = []  # (token, start, end) in the raw text
    for m in _TOKEN.finditer(raw):
        tok = m.group(0).strip(".")
        up = tok.upper()
        if not tok:
            continue
        if "@" in tok:
            info.vpa = tok
            continue
        if up in channel_words:
            info.channel = info.channel or channel_words[up]
            continue
        if re.fullmatch(r"[X*]{2,}\d{3,}", up) or (mentions_account and re.fullmatch(r"\d{9,18}", up)):
            info.account_fragments.append(up)  # masked / explicitly labelled account number
            info.references.append(up)
            continue
        if _is_reference(up):
            info.references.append(up)
            continue
        if up in stop or up in ("SELF", "OWN"):
            continue
        if re.fullmatch(r"\d{1,2}", up):
            continue
        kept.append((tok, m.start(), m.end()))
    # single letters only survive as initials next to a name ("R KUMAR", "RAMESH K")
    kept = [k for i, k in enumerate(kept) if len(k[0]) > 1 or any(
        len(o[0]) > 1 and abs(o[1] - k[2]) <= 2 or abs(k[1] - o[2]) <= 2 for o in kept[max(0, i - 1): i + 2] if o is not k)]
    if info.channel is None:
        for ch, words in rules.channels.items():
            if any(re.search(rf"\b{re.escape(w)}\b", norm) for w in words):
                info.channel = ch
                break

    if kept:
        # longest contiguous run of kept tokens = the name (references/channels split narrations)
        runs: list[list[tuple[str, int, int]]] = [[kept[0]]]
        for tok in kept[1:]:
            between = raw[runs[-1][-1][2]: tok[1]]
            if re.fullmatch(r"[\s.&']*", between):
                runs[-1].append(tok)
            else:
                runs.append([tok])
        best = max(runs, key=lambda r: (sum(1 for t in r if t[0].isalpha() and len(t[0]) >= 2), len(r),
                                        sum(len(t[0]) for t in r)))
        if any(len(t[0]) >= 3 and t[0].isalpha() for t in best):  # needs at least one real word
            info.raw_counterparty = raw[best[0][1]: best[-1][2]].strip()
            info.normalized_counterparty = N.normalize_name(info.raw_counterparty)
    elif info.vpa:
        local = info.vpa.split("@")[0]
        if re.fullmatch(r"[A-Za-z][A-Za-z.]{2,}", local):
            info.raw_counterparty = info.vpa
            info.normalized_counterparty = local.replace(".", " ").upper()
    info.counterparty_type = counterparty_type(info.normalized_counterparty, rules)
    return info


def counterparty_type(name: str | None, rules: RuleSet) -> CounterpartyType:
    if not name:
        return CounterpartyType.UNKNOWN
    cp = rules.counterparty
    toks = set(name.split())
    if any(re.search(rf"\b{re.escape(w)}\b", name) for w in cp.government_words):
        return CounterpartyType.GOVERNMENT
    if any(re.search(rf"\b{re.escape(w)}\b", name) for w in cp.lender_words):
        return CounterpartyType.BANK_OR_LENDER
    if toks & set(cp.business_suffixes):
        return CounterpartyType.BUSINESS_ENTITY
    words = name.split()
    if 1 <= len(words) <= 3 and all(w.isalpha() for w in words):
        return CounterpartyType.INDIVIDUAL
    return CounterpartyType.UNKNOWN
