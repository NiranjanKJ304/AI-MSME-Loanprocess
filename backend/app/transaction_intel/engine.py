"""Transaction intelligence engine (pure Python, no database).

Pipeline over all canonical transactions of an application:
  1. parse narration (channel, references, counterparty)
  2. collect evidence per candidate category:
       rule keywords (with direction / amount / channel constraints and exclusions),
       structural signals (counterparty type x channel x direction x amount),
       own-entity / related-party names, other own account numbers in the narration,
       a mirror entry in another account of the same applicant (internal transfer pair)
  3. reversal / refund pairing (opposite direction, same amount, within N days)
  4. first decision
  5. recurring-pattern detection; recurrence adds evidence; second decision
Scores per category are combined with noisy-OR: s = 1 - prod(1 - w). Only categories consistent
with the transaction direction are eligible. Weak evidence is NOT forced into a category:
below `classify_min_score` -> UNKNOWN; below `low_confidence_below` -> LOW_CONFIDENCE.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.document_ai import normalizers as N
from app.models.enums import (
    BusinessNature,
    ClassificationStatus,
    CounterpartyType,
    RecurrenceFrequency,
    TxnClass,
    TxnGroup,
)
from app.transaction_intel.config import RuleSet
from app.transaction_intel.narration import NarrationInfo, normalize_narration, parse_narration
from app.validation.reconciliation import name_similarity

CAP = 0.97


@dataclass
class TxnInput:
    id: str
    document_id: str
    account_number: str | None
    date: date | None
    description: str | None
    reference: str | None
    debit: Decimal | None
    credit: Decimal | None
    direction: str  # CREDIT | DEBIT | UNKNOWN
    sequence: int = 0
    page: int | None = None

    @property
    def amount(self) -> Decimal | None:
        return self.credit if self.direction == "CREDIT" else self.debit if self.direction == "DEBIT" else None


@dataclass
class EntityContext:
    own_names: list[str] = field(default_factory=list)  # applicant business / account-holder names
    related_names: list[str] = field(default_factory=list)  # proprietor / partners / configured parties
    account_numbers: dict[str, str | None] = field(default_factory=dict)  # document_id -> account number


@dataclass
class Classification:
    txn_id: str
    category: TxnClass
    group: TxnGroup
    nature: BusinessNature
    status: ClassificationStatus
    confidence: float
    narration: NarrationInfo
    evidence: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    linked_transaction_id: str | None = None
    link_type: str | None = None  # REVERSAL_OF | REVERSED_BY | REFUND_OF | REFUNDED_BY | TRANSFER_PAIR
    excluded_from_aggregates: bool = False
    exclusion_reason: str | None = None
    recurring_key: str | None = None
    counterparty_type: CounterpartyType = CounterpartyType.UNKNOWN


@dataclass
class Pattern:
    key: str
    direction: str
    counterparty: str | None
    pattern_type: str
    frequency: RecurrenceFrequency
    average_amount: Decimal
    min_amount: Decimal
    max_amount: Decimal
    amount_cv: float
    occurrences: int
    distinct_months: int
    first_seen: date
    last_seen: date
    median_interval_days: float | None
    confidence: float
    txn_ids: list[str]
    document_ids: list[str]


@dataclass
class EngineResult:
    classifications: dict[str, Classification]
    patterns: list[Pattern]
    rules_version: str


def _noisy_or(weights: list[float]) -> float:
    p = 1.0
    for w in weights:
        p *= 1 - w
    return 1 - p


class Engine:
    def __init__(self, rules: RuleSet):
        self.rules = rules
        self.compiled = rules.compiled_rules()
        self.personal = [re.compile(p, re.I) for p in rules.personal.patterns]
        self.personal_ex = [re.compile(p, re.I) for p in rules.personal.exclude]
        self.refund_rx = [re.compile(p, re.I) for p in rules.reversal.refund_patterns]

    # ------------------------------------------------------------------ evidence
    def _keyword_tokens(self, norm: str) -> set[str]:
        """Words that matched a category keyword describe the transaction type, not the counterparty."""
        toks: set[str] = set()
        names: set[str] = set()  # words of provider names ("ELECTRICITY BOARD", "LIC") stay counterparty text
        for rule, pats, _ in self.compiled:
            for p in pats:
                literals = self._literals(p.pattern)
                for m in p.finditer(norm):
                    # only the rule's own keywords - not text swallowed by ".*" (e.g. the lender name)
                    words = {w for w in m.group(0).split()
                             if any(w == lit or (len(lit) >= 3 and w.startswith(lit)) for lit in literals)}
                    (names if rule.keyword_is_counterparty else toks).update(words)
        return toks - names

    @staticmethod
    def _literals(pattern: str) -> set[str]:
        src = re.sub(r"\\[a-zA-Z]", " ", pattern.upper())  # drop \b \w \d ...
        return set(re.findall(r"[A-Z]{2,}", src))

    def _allowed(self, cat: TxnClass, direction: str) -> bool:
        d = self.rules.direction_of(cat)
        return d == "ANY" or d == direction

    def _rule_evidence(self, t: TxnInput, info: NarrationInfo) -> list[tuple[TxnClass, dict[str, Any]]]:
        out = []
        amt = t.amount
        for rule, pats, excl in self.compiled:
            if rule.direction != "ANY" and rule.direction != t.direction:
                continue
            if rule.channels and info.channel not in rule.channels:
                continue
            if amt is not None and ((rule.min_amount is not None and amt < Decimal(str(rule.min_amount)))
                                    or (rule.max_amount is not None and amt > Decimal(str(rule.max_amount)))):
                continue
            hit = next((m for p in pats if (m := p.search(info.normalized))), None)
            if not hit or any(x.search(info.normalized) for x in excl):
                continue
            out.append((rule.category, {"signal": "keyword", "rule": rule.id, "weight": rule.weight,
                                        "detail": f"'{hit.group(0)}' in narration"}))
        return out

    def _signal(self, name: str):
        return self.rules.signals.get(name)

    def _structural_evidence(self, t: TxnInput, info: NarrationInfo, ctx: EntityContext,
                             cp_type: CounterpartyType) -> list[tuple[TxnClass, dict[str, Any]]]:
        out = []
        amt = t.amount
        for name in ("business_counterparty_credit", "business_counterparty_debit", "lender_credit",
                     "lender_debit", "government_debit"):
            sig = self._signal(name)
            if sig is None or sig.category is None:
                continue
            if not self._allowed(sig.category, t.direction):
                continue
            if sig.channels and info.channel not in sig.channels:
                continue
            if sig.counterparty_types and cp_type not in sig.counterparty_types:
                continue
            if sig.min_amount is not None and (amt is None or amt < Decimal(str(sig.min_amount))):
                continue
            out.append((sig.category, {"signal": name, "weight": sig.weight,
                                       "detail": f"{cp_type.value} counterparty '{info.normalized_counterparty}'"
                                                 f" via {info.channel or 'unknown channel'}"}))
        # other own account numbers mentioned in the narration
        sig = self._signal("other_account_number_mentioned")
        own_acct = ctx.account_numbers.get(t.document_id)
        if sig and info.account_fragments:
            others = [a for d, a in ctx.account_numbers.items() if a and a != own_acct]
            for frag in info.account_fragments:
                tail = re.sub(r"\D", "", frag)[-4:]
                if len(tail) == 4 and any(a.endswith(tail) for a in others):
                    out.append((sig.category, {"signal": "other_account_number_mentioned", "weight": sig.weight,
                                               "detail": f"narration mentions {frag}, matching another account "
                                                         "of this applicant"}))
                    break
        return out

    def _counterparty_type(self, info: NarrationInfo, ctx: EntityContext,
                           evidence: list[tuple[TxnClass, dict]]) -> CounterpartyType:
        cp = info.normalized_counterparty
        if cp:
            thr = self.rules.thresholds.own_name_similarity
            if any(name_similarity(cp, n) >= thr for n in ctx.own_names):
                sig = self._signal("own_entity_counterparty")
                if sig and sig.category:
                    evidence.append((sig.category, {"signal": "own_entity_counterparty", "weight": sig.weight,
                                                    "detail": f"counterparty '{cp}' matches the applicant's own name"}))
                return CounterpartyType.OWN_ENTITY
            if any(name_similarity(cp, n) >= thr for n in ctx.related_names):
                sig = self._signal("related_party_counterparty")
                if sig and sig.category:
                    evidence.append((sig.category, {"signal": "related_party_counterparty", "weight": sig.weight,
                                                    "detail": f"counterparty '{cp}' matches a related party"}))
                return info.counterparty_type
        return info.counterparty_type

    # ------------------------------------------------------------------ decision
    def _decide(self, t: TxnInput, info: NarrationInfo, ev: list[tuple[TxnClass, dict]],
                personal: dict | None, cp_type: CounterpartyType) -> Classification:
        th = self.rules.thresholds
        per_cat: dict[TxnClass, list[float]] = defaultdict(list)
        for cat, e in ev:
            if self._allowed(cat, t.direction):
                per_cat[cat].append(e["weight"])
        scored = sorted(((_noisy_or(ws), c) for c, ws in per_cat.items()), key=lambda x: x[0], reverse=True)
        candidates = [{"category": c.value, "score": round(s, 3)} for s, c in scored[:4]]
        evidence = [{"category": c.value, **e} for c, e in ev]
        if personal:
            evidence.append({"category": None, **personal})
        top_s, top_c = scored[0] if scored else (0.0, TxnClass.UNKNOWN)
        second = scored[1][0] if len(scored) > 1 else 0.0
        if not scored or top_s < th.classify_min_score:
            nature = BusinessNature.PERSONAL if personal and t.direction == "DEBIT" else BusinessNature.UNKNOWN
            return Classification(t.id, TxnClass.UNKNOWN, TxnGroup.OTHER, nature, ClassificationStatus.UNKNOWN,
                                  round(top_s, 3), info, evidence, candidates, counterparty_type=cp_type)
        conf = round(min(CAP, top_s * (1 - 0.5 * (second / top_s))), 3)
        status = ClassificationStatus.LOW_CONFIDENCE if conf < th.low_confidence_below else ClassificationStatus.CLASSIFIED
        group = self.rules.group_of(top_c)
        nature = self.rules.nature_of(top_c)
        if personal and t.direction == "DEBIT" and group in (TxnGroup.EXPENSE, TxnGroup.OTHER, TxnGroup.CASH):
            nature = BusinessNature.PERSONAL
        if group == TxnGroup.CASH or top_c in (TxnClass.BANK_CHARGES, TxnClass.INTEREST_INCOME):
            # no counterparty for cash / bank-internal entries: leftover words ("CBE") are not a party
            if info.raw_counterparty:
                evidence.append({"category": None, "signal": "counterparty_not_applicable", "weight": 0.0,
                                 "detail": f"'{info.raw_counterparty}' not treated as a counterparty for {top_c.value}"})
            info = NarrationInfo(**{**info.__dict__, "raw_counterparty": None, "normalized_counterparty": None,
                                    "counterparty_type": CounterpartyType.UNKNOWN})
            cp_type = CounterpartyType.UNKNOWN
        return Classification(t.id, top_c, group, nature, status, conf, info, evidence, candidates,
                              counterparty_type=cp_type)

    # ------------------------------------------------------------------ main
    def run(self, txns: list[TxnInput], ctx: EntityContext) -> EngineResult:
        infos: dict[str, NarrationInfo] = {}
        evidence: dict[str, list[tuple[TxnClass, dict]]] = {}
        personal: dict[str, dict | None] = {}
        cptypes: dict[str, CounterpartyType] = {}
        for t in txns:
            info = parse_narration(t.description, self.rules, self._keyword_tokens(normalize_narration(t.description)))
            infos[t.id] = info
            ev = self._rule_evidence(t, info)
            cpt = self._counterparty_type(info, ctx, ev)
            cptypes[t.id] = cpt
            strong = {c for c, e in ev if e["weight"] >= 0.6}
            # weak structural signals never compete with a strong keyword for a different category
            ev += [(c, e) for c, e in self._structural_evidence(t, info, ctx, cpt)
                   if not strong or c in strong or e["weight"] >= 0.6]
            evidence[t.id] = ev
            pm = next((m for p in self.personal if (m := p.search(info.normalized))), None)
            personal[t.id] = ({"signal": "personal_spend", "weight": self.rules.personal.weight,
                               "detail": f"'{pm.group(0)}' suggests personal spending"}
                              if pm and not any(x.search(info.normalized) for x in self.personal_ex) else None)

        links = self._pair_transfers(txns, evidence)
        reversals = self._pair_reversals(txns, infos, evidence)

        result = {t.id: self._decide(t, infos[t.id], evidence[t.id], personal[t.id], cptypes[t.id]) for t in txns}
        patterns = self._recurring(txns, result)
        for p in patterns:
            members = set(p.txn_ids)
            for t in txns:
                if t.id not in members:
                    continue
                cls = result[t.id]
                cls.recurring_key = p.key
                extra = []
                if p.direction == "CREDIT" and p.counterparty and p.pattern_type == "CUSTOMER_RECEIPTS" and \
                        cptypes[t.id] not in (CounterpartyType.OWN_ENTITY, CounterpartyType.BANK_OR_LENDER,
                                              CounterpartyType.GOVERNMENT):
                    sig = self._signal("recurring_payer")
                    if sig and sig.category:
                        extra.append((sig.category, {"signal": "recurring_payer", "weight": sig.weight,
                                                     "detail": f"{p.occurrences} receipts from '{p.counterparty}' "
                                                               f"({p.frequency.value.lower()})"}))
                if p.direction == "DEBIT" and p.pattern_type == "SUPPLIER_PAYMENTS":
                    sig = self._signal("recurring_payee")
                    if sig and sig.category:
                        extra.append((sig.category, {"signal": "recurring_payee", "weight": sig.weight,
                                                     "detail": f"{p.occurrences} payments to '{p.counterparty}'"}))
                if cls.status != ClassificationStatus.UNKNOWN and p.frequency != RecurrenceFrequency.IRREGULAR:
                    sig = self._signal("recurring_confirms_top")
                    if sig:
                        extra.append((cls.category, {"signal": "recurring_pattern", "weight": sig.weight,
                                                     "detail": f"part of a {p.frequency.value.lower()} pattern "
                                                               f"({p.occurrences} occurrences)"}))
                if extra:
                    evidence[t.id] += extra
                    new = self._decide(t, infos[t.id], evidence[t.id], personal[t.id], cptypes[t.id])
                    new.recurring_key = p.key
                    result[t.id] = new

        for a_id, b_id in links:
            for x, y in ((a_id, b_id), (b_id, a_id)):
                result[x].linked_transaction_id, result[x].link_type = y, "TRANSFER_PAIR"
        by_id = {t.id: t for t in txns}
        for r_id, o_id, is_refund in reversals:
            r = result[r_id]
            r.category = TxnClass.REFUND if is_refund and by_id[r_id].direction == "CREDIT" else TxnClass.REVERSAL
            r.group, r.nature = self.rules.group_of(r.category), BusinessNature.UNKNOWN
            r.status, r.confidence = ClassificationStatus.CLASSIFIED, max(r.confidence, 0.9)
            r.linked_transaction_id, r.link_type = o_id, ("REFUND_OF" if r.category == TxnClass.REFUND else "REVERSAL_OF")
            r.excluded_from_aggregates, r.exclusion_reason = True, f"{r.link_type} {o_id}"
            o = result[o_id]
            o.linked_transaction_id = r_id
            o.link_type = "REFUNDED_BY" if r.category == TxnClass.REFUND else "REVERSED_BY"
            o.excluded_from_aggregates, o.exclusion_reason = True, f"{o.link_type} {r_id}"
            o.evidence.append({"category": None, "signal": "reversed", "weight": 1.0,
                               "detail": f"cancelled by transaction {r_id}; kept but excluded from cash-flow totals"})
        return EngineResult(result, patterns, self.rules.version)

    # ------------------------------------------------------------------ transfers between own accounts
    def _pair_transfers(self, txns: list[TxnInput], evidence) -> list[tuple[str, str]]:
        sig = self._signal("matching_opposite_entry_other_account")
        if sig is None or sig.category is None:
            return []
        max_days = sig.max_days or 2
        transfer_cats = {c for c in TxnClass if self.rules.group_of(c) == TxnGroup.TRANSFER}

        def explained_otherwise(t: TxnInput) -> bool:
            """Strong keyword evidence for a non-transfer category (e.g. 'SALARY') vetoes pairing."""
            ev = evidence[t.id]
            if any(c in transfer_cats for c, _ in ev):
                return False
            return any(e["signal"] == "keyword" and e["weight"] >= 0.6 for _, e in ev)

        debits = [t for t in txns if t.direction == "DEBIT" and t.date and t.amount and not explained_otherwise(t)]
        credits = [t for t in txns if t.direction == "CREDIT" and t.date and t.amount and not explained_otherwise(t)]
        used: set[str] = set()
        pairs = []
        for d in debits:
            best = None
            for c in credits:
                if c.id in used or c.document_id == d.document_id or c.amount != d.amount:
                    continue
                gap = abs((c.date - d.date).days)
                if gap <= max_days and (best is None or gap < best[0]):
                    best = (gap, c)
            if best:
                c = best[1]
                used.add(c.id)
                pairs.append((d.id, c.id))
                for x, y in ((d, c), (c, d)):
                    evidence[x.id].append((sig.category, {
                        "signal": "matching_opposite_entry_other_account", "weight": sig.weight,
                        "detail": f"mirror {y.direction.lower()} of {y.amount} on {y.date} in another account "
                                  f"of the applicant (transaction {y.id})"}))
        return pairs

    # ------------------------------------------------------------------ reversals / refunds
    def _pair_reversals(self, txns: list[TxnInput], infos, evidence) -> list[tuple[str, str, bool]]:
        window = self.rules.reversal.window_days
        cands = [t for t in txns if any(c in (TxnClass.REVERSAL, TxnClass.REFUND) for c, _ in evidence[t.id])]
        cand_ids = {t.id for t in cands}
        used: set[str] = set()
        out = []
        for r in sorted(cands, key=lambda t: (t.date or date.min, t.sequence)):
            if r.amount is None or r.date is None:
                continue
            best = None
            for o in txns:
                if o.id in cand_ids or o.id in used or o.direction == r.direction or o.direction == "UNKNOWN":
                    continue
                if o.amount != r.amount or o.date is None or not (0 <= (r.date - o.date).days <= window):
                    continue
                if o.document_id != r.document_id:
                    continue
                same_cp = bool(infos[o.id].normalized_counterparty) and \
                    infos[o.id].normalized_counterparty == infos[r.id].normalized_counterparty
                shared_ref = bool(set(infos[o.id].references) & set(infos[r.id].references))
                key = (not (same_cp or shared_ref), (r.date - o.date).days, -o.sequence)
                if best is None or key < best[0]:
                    best = (key, o)
            if best:
                o = best[1]
                used.add(o.id)
                is_refund = any(rx.search(infos[r.id].normalized) for rx in self.refund_rx)
                out.append((r.id, o.id, is_refund))
                evidence[r.id].append((TxnClass.REFUND if is_refund else TxnClass.REVERSAL, {
                    "signal": "reversal_pair", "weight": 0.9,
                    "detail": f"opposite entry of the same amount {o.amount} on {o.date} (transaction {o.id})"}))
        return out

    # ------------------------------------------------------------------ recurring patterns
    def _recurring(self, txns: list[TxnInput], result: dict[str, Classification]) -> list[Pattern]:
        cfg = self.rules.recurring
        groups: dict[str, list[TxnInput]] = defaultdict(list)
        for t in txns:
            cls = result[t.id]
            if t.date is None or t.amount is None or t.direction == "UNKNOWN":
                continue
            if cls.category in (TxnClass.REVERSAL, TxnClass.REFUND):
                continue
            cp = cls.narration.normalized_counterparty
            if cp:
                groups[f"{t.direction}|CP|{cp}"].append(t)
            elif cls.status != ClassificationStatus.UNKNOWN and cls.category in cfg.group_by_category_without_counterparty:
                groups[f"{t.direction}|CAT|{cls.category.value}"].append(t)
        patterns = []
        for key, members in groups.items():
            members.sort(key=lambda t: (t.date, t.sequence))
            months = {(t.date.year, t.date.month) for t in members}
            if len(members) < cfg.min_occurrences or len(months) < cfg.min_distinct_months:
                continue
            intervals = [(b.date - a.date).days for a, b in zip(members, members[1:])]
            med = statistics.median(intervals) if intervals else None
            freq, regular = RecurrenceFrequency.IRREGULAR, 0.0
            for name, (lo, hi) in cfg.intervals.items():
                if med is not None and lo <= med <= hi:
                    share = sum(1 for i in intervals if lo <= i <= hi) / len(intervals)
                    if share >= cfg.regular_share:
                        freq, regular = RecurrenceFrequency(name), share
                    break
            amounts = [float(t.amount) for t in members]
            mean = statistics.fmean(amounts)
            cv = (statistics.pstdev(amounts) / mean) if mean else 0.0
            stab = 1.0 if cv <= cfg.stable_amount_cv else max(0.0, 1 - (cv - cfg.stable_amount_cv) / 0.5)
            conf = round(min(CAP, 0.4 * regular + 0.3 * stab + 0.3 * min(1.0, len(members) / 6)), 3)
            direction = members[0].direction
            cats = [result[t.id].category for t in members if result[t.id].status != ClassificationStatus.UNKNOWN]
            dominant = max(set(cats), key=cats.count) if cats else None
            ptype = _pattern_type(direction, dominant, result[members[0].id])
            cp = key.split("|", 2)[2] if "|CP|" in key else None
            patterns.append(Pattern(
                key=key, direction=direction, counterparty=cp, pattern_type=ptype, frequency=freq,
                average_amount=Decimal(str(round(mean, 2))), min_amount=min(t.amount for t in members),
                max_amount=max(t.amount for t in members), amount_cv=round(cv, 4), occurrences=len(members),
                distinct_months=len(months), first_seen=members[0].date, last_seen=members[-1].date,
                median_interval_days=float(med) if med is not None else None, confidence=conf,
                txn_ids=[t.id for t in members], document_ids=sorted({t.document_id for t in members}),
            ))
        return patterns


def _pattern_type(direction: str, dominant: TxnClass | None, sample: Classification) -> str:
    mapping = {TxnClass.SALARY: "SALARY", TxnClass.RENT: "RENT", TxnClass.LOAN_REPAYMENT: "EMI",
               TxnClass.INTEREST_PAYMENT: "LOAN_INTEREST", TxnClass.UTILITIES: "UTILITY_BILL",
               TxnClass.INSURANCE: "INSURANCE_PREMIUM", TxnClass.BANK_CHARGES: "BANK_CHARGES",
               TxnClass.SUPPLIER_PAYMENT: "SUPPLIER_PAYMENTS", TxnClass.TAX_PAYMENT: "TAX_PAYMENTS"}
    if dominant in mapping:
        return mapping[dominant]
    if dominant == TxnClass.OPERATING_EXPENSE and re.search(r"SUBSCRIPTION|SOFTWARE|SAAS", sample.narration.normalized):
        return "SUBSCRIPTION"
    if dominant in (TxnClass.OWN_ACCOUNT_TRANSFER, TxnClass.INTERNAL_TRANSFER, TxnClass.RELATED_ACCOUNT_TRANSFER):
        return "TRANSFERS"
    if direction == "CREDIT" and dominant in (None, TxnClass.BUSINESS_REVENUE, TxnClass.OTHER_BUSINESS_INCOME):
        return "CUSTOMER_RECEIPTS"
    if direction == "DEBIT" and sample.counterparty_type == CounterpartyType.BUSINESS_ENTITY:
        return "SUPPLIER_PAYMENTS"
    return "OTHER"
