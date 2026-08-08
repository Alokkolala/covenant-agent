"""Covenant evaluation.

Four regular patterns account for 21 of the open set's 24 non-6.1 cells:

  A  related-party payments, absolute cap
  B  related-party payments as a share of revenue
  C  minimum revenue for the period       (breach is BELOW the floor)
  D  maximum capex for the period

Clause 6.1 is a different metric per borrower (COVENANTS.md: "12 metrics, 12
shapes") and is handled by the solve_* functions near the bottom of this file,
each dispatched from run.py by matching the covenant's own wording - never by
scenario_id - the same way patterns A-D already are.

Nothing here calls a model. Categories come from explicit wording in the
transaction description, corrected where a document names a judgment
(reclassification, correction, cut-off) - see Adjustments below.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# Verified against every Pattern C/D cell in the open set.
REVENUE = re.compile(r"sales settlement", re.I)
CAPEX = re.compile(r"\bpurchase of\b", re.I)
# Denominator for P6 6.1 and (below) most other 6.1 EBITDA-shaped covenants.
# "operating and maintenance expenses" is B1's own variant of the same category -
# without it B1's opex reads 0 and EBITDA is wildly overstated. Confirmed there is
# no borrower where both phrasings appear (no double count risk).
OPEX = re.compile(r"operating (?:costs|and maintenance expenses)", re.I)
# New categories needed only by clause 6.1 (COVENANTS.md's "12 metrics, 12 shapes").
# Same discipline as REVENUE/CAPEX/OPEX above: a short phrase verified against the
# specific rows each borrower's own 6.1 clause turned out to need, not a guess at
# what "should" be in a general ledger. `\b` word boundaries matter: "sublease"
# must not match LEASE, "current account" must not match a bare "rent" scan.
LEASE = re.compile(r"\b(?:rent|lease)\b", re.I)
INSURANCE = re.compile(r"\binsurance\b", re.I)
INTEREST = re.compile(r"\binterest\b", re.I)
PAYROLL = re.compile(r"\bpayroll\b", re.I)
TAX = re.compile(r"\btax\b", re.I)
UTILITIES = re.compile(r"\b(?:electricity|utilit(?:y|ies))\b", re.I)
FINANCING = re.compile(r"facility drawdown", re.I)
SUBSIDIARY_TRANSFER = re.compile(r"transfer(?:red)? of .* to subsidiary", re.I)

# name -> pattern, for the generic reclassification-aware aggregator below.
# "ebitda_addback" never matches a description on its own - a row only ever
# joins it via an explicit reclassification (P4's one-off items, Note 8).
CATEGORIES = {
    "revenue": REVENUE, "capex": CAPEX, "opex": OPEX, "lease": LEASE,
    "insurance": INSURANCE, "interest": INTEREST, "payroll": PAYROLL,
    "tax": TAX, "utilities": UTILITIES, "financing": FINANCING,
    "ebitda_addback": re.compile(r"(?!)"),
}
# revenue and financing are money coming IN (sum positives); every other
# category is an expense (sum the absolute value of negatives). A category is
# always one or the other in this dataset, never both.
INFLOW_CATEGORIES = {"revenue", "financing"}
# Russian category names as they appear in accepted-reclassification sentences
# -> the canonical key above. Covers every source/target category name seen
# anywhere in the open set's audit notes and final reports.
CATEGORY_RU = {
    "операционные расходы": "opex",
    "страховые премии": "insurance",
    "процентные расходы": "interest",
    "расходы на оплату труда": "payroll",
    "коммунальные услуги": "utilities",
    "коммунальные расходы": "utilities",
    "налоги": "tax",
    "капитальные затраты": "capex",
    "арендные платежи": "lease",
    "аренда": "lease",
    "выручка": "revenue",
}

# Ownership table: bounded to the segment between the header and the rule
# sentence, so adjacent rows cannot merge into one entity.
OWN_SEGMENT = re.compile(r"Доля голосующих прав(.*?)Организации,\s*в\s*которых", re.S)
OWN_ROW = re.compile(r"([^%\d]{3,60}?)\s*(\d{1,3}\.\d)\s*%")
OWN_THRESHOLD = re.compile(r"владеет\s*(\d{1,3}\.\d)\s*%\s*и\s*более")

LEGAL = re.compile(r"\b(l\.?l\.?p|llc|jsc|inc|ltd|lp|co|company|partners|group|bureau)\b\.?", re.I)
PAREN = re.compile(r"\([^)]*\)")


# EUR->USD rate: deliberately not a tabulated constant. CASE.ru says currencies
# differ and `actual` is always in dollars; the only rate ever disclosed (P3's
# audit note 9.1) is derived from one paired settlement, with the note explicit
# that "a separate rate table is not kept" - so this is the best a document-only
# pipeline can do. Set once by run.py from whichever scenario's notes carry the
# note; None means "no EUR row has ever mattered" and amount() then does the
# same thing it always did (face value), so this is a strict no-op until used.
EUR_USD_RATE: Decimal | None = None


def amount(row: dict) -> Decimal:
    """Ledger amounts are dirty: two rows in the open set have an EMPTY amount,
    and both sit in the category their borrower's 6.1 covenant measures. Treat
    an unparseable value as zero so one bad row cannot kill a scenario, but the
    caller is expected to surface it - a silently-zeroed expense understates
    every aggregate it belongs to."""
    try:
        v = Decimal(row["amount"])
    except (InvalidOperation, TypeError, KeyError):
        return Decimal(0)
    if row.get("currency") == "EUR" and EUR_USD_RATE is not None:
        return v * EUR_USD_RATE
    return v


def malformed(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        try:
            Decimal(r["amount"])
        except (InvalidOperation, TypeError, KeyError):
            out.append(r)
    return out


def q2(x: Decimal) -> Decimal:
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def norm_name(s: str) -> str:
    """KYC says 'Aktau Holdings LLP', the ledger 'Aktau Holdings L.L.P. (Kostanay
    centre)'. Strip site suffixes, punctuation and legal form, then compare."""
    s = PAREN.sub(" ", s or "")
    s = LEGAL.sub(" ", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip().lower()


@dataclass
class Owner:
    name: str
    share: Decimal

    def related(self, threshold: Decimal) -> bool:
        return self.share >= threshold  # ">= and more", inclusive, per the dossiers


# A stake can be held THROUGH an intermediate company, in which case the table's
# headline percentage is not the one the covenant tests. B4's dossier: 48.0% in
# one counterparty, held via an entity the Group owns 27.3% of - effective 13.1%,
# which is BELOW its 30% threshold. Taking the headline number makes that
# counterparty a related party when it is not.
LOOK_THROUGH = re.compile(
    r"Доля\s+в\s+(.{3,60}?)\s+удерживается\s+косвенно\s+через\s+(.{3,60}?)[;,].{0,80}?"
    r"(\d{1,3}\.\d)\s*%", re.S)


def parse_kyc(text: str) -> tuple[Decimal | None, list[Owner]]:
    """Voting threshold and the ownership table. Every borrower uses a different
    threshold and plants a near-miss just below its own.

    Indirect holdings are resolved to their effective stake. A dossier that
    discloses a chain is testing whether the headline figure was taken at face
    value - see LOOK_THROUGH."""
    thr = None
    if m := OWN_THRESHOLD.search(text):
        thr = Decimal(m.group(1))

    chains: dict[str, Decimal] = {}
    for held, _via, pct in LOOK_THROUGH.findall(text):
        chains[norm_name(held)] = Decimal(pct) / Decimal(100)

    owners = []
    if seg := OWN_SEGMENT.search(text):
        for name, pct in OWN_ROW.findall(seg.group(1)):
            name = name.strip(" .,;:«»\"'|")
            if not name:
                continue
            share = Decimal(pct)
            if (factor := chains.get(norm_name(name))) is not None:
                share = (share * factor).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            owners.append(Owner(name, share))
    return thr, owners


# A second KYC table, structurally unrelated to the ownership one above: which
# subsidiaries are pledged as security (Restricted) versus not (Unrestricted).
# Feeds P9 6.1 only. Header, columns and threshold direction all differ from
# OWN_SEGMENT/OWN_ROW/OWN_THRESHOLD, so it is not a variant of parse_kyc - it is
# a different fact living in the same document.
SUBSIDIARY_SEGMENT = re.compile(r"Доля активов в залоге(.*?)Дочерние организации,\s*у\s*которых", re.S)
SUBSIDIARY_ROW = re.compile(r"([^%\d:]{3,60}?)\s*:\s*(\d{1,3}\.\d)\s*%")
SUBSIDIARY_THRESHOLD = re.compile(r"доля активов в залоге ниже\s*(\d{1,3}\.\d)\s*%")


def parse_subsidiary_status(text: str) -> tuple[Decimal | None, dict[str, Decimal]]:
    """Pledge share per subsidiary, and the threshold below which a subsidiary
    counts as Unrestricted. Below the threshold, not >= it - the opposite
    direction from the related-party ownership test, verified against the
    clause's own wording rather than assumed from that similarity."""
    thr = None
    if m := SUBSIDIARY_THRESHOLD.search(text):
        thr = Decimal(m.group(1))
    pledges = {}
    if seg := SUBSIDIARY_SEGMENT.search(text):
        for name, pct in SUBSIDIARY_ROW.findall(seg.group(1)):
            name = name.strip(" .,;:«»\"'|")
            if name:
                pledges[name] = Decimal(pct)
    return thr, pledges


def related_rows(rows: list[dict], owners: list[Owner], threshold: Decimal) -> list[dict]:
    targets = {norm_name(o.name) for o in owners if o.related(threshold)}
    return [r for r in rows if norm_name(r["counterparty"]) in targets]


def outflow(rows: list[dict]) -> Decimal:
    """Absolute value of money leaving the account. `actual` is always positive."""
    return q2(abs(sum(amount(r) for r in rows if amount(r) < 0)))


# ---------------------------------------------------------------------------
# Adjustments: reclassifications, corrections and period cut-offs named in a
# borrower's audit notes / final report / treasury memo. This is clause 6.1's
# load-bearing mechanism - COVENANTS.md's "12 metrics, 12 shapes" almost all
# turn out to need one of these (TRAPS.md: "the evidence transaction is the
# one whose reclassification flips the verdict").
#
# Authority, not just wording, decides what to trust: `cert_draft` documents
# say so themselves - "ПРОЕКТ ... заменена окончательным отчётом ... НЕ
# ЯВЛЯЕТСЯ ОКОНЧАТЕЛЬНОЙ ПОЗИЦИЕЙ" - and explicitly instruct that an
# unconfirmed proposal reverts to the original classification. The open set's
# five drafts have exactly one matching `cert_final` (B1's), for a different
# report number covering a different transaction than that draft's own
# proposal, so drafts are never fed into this parser - only audit_notes,
# cert_final and treasury_notes are. See TRAPS.md for the worked example
# (B1 6.1: the draft's opex->utilities proposal is unconfirmed and dropped;
# the *audit note's* consulting->interest item is confirmed by cert_final and
# applied).
# ---------------------------------------------------------------------------


@dataclass
class Adjustments:
    reclass: dict = field(default_factory=dict)      # txn_id -> category name
    corrections: dict = field(default_factory=dict)  # txn_id -> Decimal (signed)
    excluded: set = field(default_factory=set)        # txn_id, dropped from the period
    disclosed: Decimal = Decimal(0)                   # off-ledger amount, no txn_id at all

    def without(self, txn_id: str) -> "Adjustments":
        """A copy with one transaction's judgment undone - used only by the
        evidence flip-test in evidence_from_judgment, never by the primary
        computation."""
        return Adjustments(
            reclass={k: v for k, v in self.reclass.items() if k != txn_id},
            corrections={k: v for k, v in self.corrections.items() if k != txn_id},
            excluded=self.excluded - {txn_id},
            disclosed=self.disclosed,
        )


def judgment_ids(adj: Adjustments) -> set:
    """Candidates for evidence_txn_id: transactions carrying a decision, and
    nothing else - TRAPS.md step 1. A row that only contributes to a sum is
    never a candidate."""
    return set(adj.reclass) | set(adj.corrections) | set(adj.excluded)


def resolve_by_amount(rows: list[dict], target: Decimal, counterparty_hint: str = "") -> dict | None:
    """Some notes name an amount and counterparty but not a txn_id (a Template-1
    reclassification sentence, or a table row transcribed from a scan). Resolve
    it the way a human would: the one ledger row with that exact amount; if
    several tie, break by counterparty. No match, or an unbroken tie, means
    skip rather than guess."""
    hits = [r for r in rows if abs(amount(r)) == target]
    if len(hits) > 1 and counterparty_hint:
        narrowed = [r for r in hits if norm_name(counterparty_hint) in norm_name(r["counterparty"])]
        if narrowed:
            hits = narrowed
    return hits[0] if len(hits) == 1 else None


# "Сумма в размере $X, выплаченная контрагенту Y, первоначально учтённая как A,
# переклассифицирована для целей соблюдения ковенантов как B." Verified against
# every accepted reclassification in the open set's audit notes and its one
# final report (B1, P2, P10). The completed-action participle
# "переклассифицирован-" is the load-bearing word: a *considered and rejected*
# reclassification uses the noun "переклассификации" instead (P10 note 7.2),
# which this stem does not match - no separate "was it rejected" check needed.
_RECLASS = re.compile(
    r"Сумма в размере \$([\d,]+\.\d{2}),\s*выплаченн\w+ контрагенту\s+([^,]+?),\s*"
    r"первоначально учтённ\w+ как\s+([^,]+?),\s*"
    r"переклассифицирован\w+\s+(?:для целей соблюдения ковенантов\s+)?как\s+([^.]+)\.",
    re.I,
)
# "Операция TXN-xxx (...): сумма не отражена в выгрузке реестра; фактическая
# сумма операции составляет $X (расход)." Two empty-amount rows in the open
# set recover this way - one from audit_notes (P8), one from a treasury memo
# (P7) that pipeline/index.py originally misclassified as noise.
_CORRECTION = re.compile(
    r"Операция (TXN-[\w-]+)\s*\([^)]*\):\s*сумма не отражена в выгрузке реестра;\s*"
    r"фактическая сумма операции составляет \$([\d,]+\.\d{2})",
    re.I,
)
# "Операция TXN-xxx, датированная YYYY-MM-DD, исключена из ковенантного
# периода 2025 года." (B4's revenue cut-off.)
_CUTOFF_EXCLUDED = re.compile(
    r"Операция (TXN-[\w-]+),\s*датированн\w+ [\d.\-]+,\s*исключен\w+ из ковенантного периода",
    re.I,
)
# "Операция TXN-xxx (...) относится к услугам, оказанным в период с A по B." -
# a transaction reassigned to a period outside the covenant window (P1's).
_CUTOFF_MOVED = re.compile(
    r"Операция (TXN-[\w-]+)[^.]*?относится к услугам, оказанным в период "
    r"с (\d{4}-\d{2}-\d{2}) по (\d{4}-\d{2}-\d{2})",
    re.I,
)
# "...программе выходных пособий ... в размере $X раскрывается и не
# отражается отдельной операцией." An aggregate that exists only as a
# disclosure, never as a ledger row (P8's severance liability).
_DISCLOSED = re.compile(
    r"в размере\s*\$([\d,]+\.\d{2})\s*раскрывается и не отражается отдельной операцией",
    re.I,
)


def parse_adjustments(texts: list[str], rows: list[dict],
                       period_start: str = "2025-01-01", period_end: str = "2025-12-31") -> Adjustments:
    """texts: raw document text from audit_notes / cert_final / treasury_notes
    ONLY - never cert_draft (see the module note above). Flattened internally;
    these are prose sentences that wrap mid-phrase in the raw PDF extract, same
    reasoning as pipeline/index.py's `flatten`."""
    adj = Adjustments()
    for text in texts:
        flat = " ".join((text or "").split())
        for m in _RECLASS.finditer(flat):
            target_amt = Decimal(m.group(1).replace(",", ""))
            counterparty, category = m.group(2), m.group(4)
            cat = CATEGORY_RU.get(category.strip(" .").lower())
            if cat is None:
                continue
            row = resolve_by_amount(rows, target_amt, counterparty)
            if row:
                adj.reclass[row["txn_id"]] = cat
        for m in _CORRECTION.finditer(flat):
            adj.corrections[m.group(1)] = -Decimal(m.group(2).replace(",", ""))
        for m in _CUTOFF_EXCLUDED.finditer(flat):
            adj.excluded.add(m.group(1))
        for m in _CUTOFF_MOVED.finditer(flat):
            txn, start, end = m.group(1), m.group(2), m.group(3)
            if end < period_start or start > period_end:
                adj.excluded.add(txn)
        if m := _DISCLOSED.search(flat):
            adj.disclosed += Decimal(m.group(1).replace(",", ""))
    return adj


# "Характер статьи | Контрагент | Сумма" table rows, plus the materiality
# floor sentence. Table structure must survive, so this collapses horizontal
# whitespace per line rather than flattening away the newlines entirely.
_ADDBACK_ROW = re.compile(r"^[^|\n]+\|\s*[«\"]?([^|»\"]+?)[»\"]?\s*\|\s*\$([\d,]+\.\d{2})\s*$", re.M)
_ADDBACK_FLOOR = re.compile(r"статьи в сумме не менее\s*\$([\d,]+\.\d{2})")


def parse_ebitda_addbacks(text: str, rows: list[dict]) -> dict:
    """P4's one-off EBITDA add-backs (Примечание 8): items below the disclosed
    materiality floor are excluded - the mechanism behind the planted
    near-miss (one item sits just under it). Reads the floor from the
    document instead of hardcoding $300,000, so a differently-worded table
    still works. Returns a reclass-shaped dict, merged straight into
    Adjustments.reclass by the caller."""
    floor = Decimal(0)
    if m := _ADDBACK_FLOOR.search(text or ""):
        floor = Decimal(m.group(1).replace(",", ""))
    lines = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in (text or "").splitlines())
    out = {}
    for m in _ADDBACK_ROW.finditer(lines):
        counterparty, amt = m.group(1).strip(), Decimal(m.group(2).replace(",", ""))
        if amt < floor:
            continue
        row = resolve_by_amount(rows, amt, counterparty)
        if row:
            out[row["txn_id"]] = "ebitda_addback"
    return out


def category_rows(rows: list[dict], name: str, adj: Adjustments) -> list[dict]:
    """Rows belonging to category `name`, honouring accepted reclassifications
    and dropping cut-off exclusions: a row moves wherever its judgment sends
    it, regardless of what its own description says; everything else falls
    back to the description regex. Corrected amounts are substituted here too,
    so every caller downstream sees the true value without needing `adj`."""
    pattern = CATEGORIES[name]
    out = []
    for r in rows:
        if r["txn_id"] in adj.excluded:
            continue
        if r["txn_id"] in adj.corrections:
            r = dict(r, amount=str(adj.corrections[r["txn_id"]]))
        target = adj.reclass.get(r["txn_id"])
        if target is not None:
            if target == name:
                out.append(r)
        elif pattern.search(r["description"]):
            out.append(r)
    return out


def category_total(rows: list[dict], name: str, adj: Adjustments) -> Decimal:
    members = category_rows(rows, name, adj)
    if name in INFLOW_CATEGORIES:
        return q2(sum((amount(r) for r in members if amount(r) > 0), Decimal(0)))
    return outflow(members)


def ebitda(rows: list[dict], adj: Adjustments) -> Decimal:
    """Revenue minus Opex - the definition every EBITDA-based 6.1 covenant in
    the open set states inline, word for word, wherever it restates the term
    at all (B1, P4, P5's own-entity half)."""
    return category_total(rows, "revenue", adj) - category_total(rows, "opex", adj)


def evidence_from_judgment(rows: list[dict], adj: Adjustments, actual_of, threshold: Decimal,
                            direction: str) -> str | None:
    """Step 2 of the evidence rule (TRAPS.md), adapted for adjustment-driven
    6.1 covenants: undo one judgment - a reclassification, a corrected amount,
    or a period cut-off - at a time. If undoing it flips the verdict, that
    transaction is the evidence; if nothing flips, evidence is null even
    though a judgment exists (P1 6.3's proof that contribution alone is not
    evidence, generalised)."""
    current = verdict(actual_of(rows, adj), threshold, direction)
    for txn_id in judgment_ids(adj):
        if verdict(actual_of(rows, adj.without(txn_id)), threshold, direction) != current:
            return txn_id
    return None


def verdict(actual: Decimal, threshold: Decimal, direction: str) -> str:
    """direction 'max' -> breach above; 'min' -> breach below. Never inferable
    from the clause number: five of the twelve 6.1 covenants breach below."""
    if direction == "max":
        return "BREACH" if actual > threshold else "COMPLIANT"
    return "BREACH" if actual < threshold else "COMPLIANT"


def evidence_for(candidates: list[dict], recompute, current: str) -> str | None:
    """Step 2 of the evidence rule: a candidate is the evidence only if removing
    its judgment FLIPS the verdict. Candidates must already be restricted to
    transactions carrying a decision - see TRAPS.md. Removal flipping the verdict
    is necessary but not sufficient on its own."""
    for row in candidates:
        if recompute([r for r in candidates if r is not row]) != current:
            return row["txn_id"]
    return None


def solve_related_party(rows, owners, threshold, cap, denominator=None):
    """Pattern A (cap in dollars) and Pattern B (cap as a share of a base).

    The base is NOT always revenue: P6 6.1 caps related-party payments as a share
    of OPERATING EXPENSES. Dividing by revenue there gave the wrong number and
    flipped the status, zeroing the cell.
    """
    rel = related_rows(rows, owners, threshold)
    total = outflow(rel)
    if denominator is None:
        raw, recompute = total, lambda keep: verdict(outflow(keep), cap, "max")
    else:
        if not denominator:
            return None
        raw = total / denominator
        recompute = lambda keep: verdict(outflow(keep) / denominator, cap, "max")
    # Compare BEFORE rounding. q2(0.0403) == 0.04 == the cap reads COMPLIANT,
    # but the true value is above it. Round only for reporting.
    status = verdict(raw, cap, "max")
    actual = q2(raw)
    # Every related-party inclusion rests on a KYC judgment, so all of them are
    # candidates; the flip test decides which one, if any, is the evidence.
    return {"status": status, "actual": float(actual),
            "evidence_txn_id": evidence_for(rel, recompute, status)}


def solve_min_revenue(rows, adj, floor):
    """Pattern C. Every borrower's own 6.2 wording ties 'revenue' to the
    audited-with-reclassifications figure (P1: "с учётом переквалификаций
    ... аудиторами"; P3: reclassified-out amounts excluded "независимо от
    их первоначального отражения в учёте") - the same reclassification
    mechanism category_total already applies generically elsewhere, so this
    must go through it rather than a bare description-regex sum."""
    def actual_of(rs, a):
        return category_total(rs, "revenue", a)
    raw = actual_of(rows, adj)
    status = verdict(raw, floor, "min")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, floor, "min")}


def solve_max_capex(rows, adj, cap):
    """Pattern D. Every borrower's own 6.2/6.3 wording is explicit: amounts
    reclassified BY THE AUDITOR into "Капитальные затраты" are included, and
    amounts reclassified out are excluded, "независимо от её первоначального
    отражения в учёте" - i.e. the same category_total mechanism
    solve_capital_intensity already uses for capex in clause 6.1."""
    def actual_of(rs, a):
        return category_total(rs, "capex", a)
    raw = actual_of(rows, adj)
    status = verdict(raw, cap, "max")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, cap, "max")}


# ---------------------------------------------------------------------------
# Clause 6.1 - one metric per borrower (COVENANTS.md). Each function below is
# dispatched by run.py matching the covenant's own distinctive wording, never
# by scenario_id, the same way patterns A-D are - see covenant_kind() in
# run.py. All arithmetic is Decimal; `rows` are this scenario's ledger rows
# for the covenant period, `adj` is that scenario's Adjustments (built once
# from its audit_notes / cert_final / treasury_notes - see the module note
# above `Adjustments`).
# ---------------------------------------------------------------------------


def solve_capital_intensity(rows, adj, cap):
    """P1: capex / (opex + lease), max. The clause's own final sentence -
    reclassified amounts move in both numerator and denominator - is exactly
    what category_total/category_rows already do generically."""
    def actual_of(rs, a):
        denom = category_total(rs, "opex", a) + category_total(rs, "lease", a)
        return category_total(rs, "capex", a) / denom if denom else Decimal(0)
    raw = actual_of(rows, adj)
    status = verdict(raw, cap, "max")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, cap, "max")}


def solve_cover_ratio(rows, adj, floor):
    """P2: (revenue + financing) / (opex + capex), min."""
    def actual_of(rs, a):
        num = category_total(rs, "revenue", a) + category_total(rs, "financing", a)
        denom = category_total(rs, "opex", a) + category_total(rs, "capex", a)
        return num / denom if denom else Decimal(0)
    raw = actual_of(rows, adj)
    status = verdict(raw, floor, "min")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, floor, "min")}


def solve_springing_leverage(rows, adj, trigger, cap):
    """P3: the leverage test (financing / EBITDA, max) applies only once
    financing drawdowns pass `trigger` - CASE.ru's carve-out. Below the
    trigger, status is COMPLIANT regardless of the ratio, but `actual` stays
    the real ratio, never the trigger value or a null."""
    def financing_of(rs, a):
        return category_total(rs, "financing", a)

    def ratio_of(rs, a):
        e = ebitda(rs, a)
        return financing_of(rs, a) / e if e else Decimal(0)

    def status_of(a):
        return verdict(ratio_of(rows, a), cap, "max") if financing_of(rows, a) > trigger else "COMPLIANT"

    raw = ratio_of(rows, adj)
    status = status_of(adj)
    ev = None
    for txn_id in judgment_ids(adj):
        if status_of(adj.without(txn_id)) != status:
            ev = txn_id
            break
    return {"status": status, "actual": float(q2(raw)), "evidence_txn_id": ev}


def solve_ebitda_margin(rows, adj, floor):
    """P4: Adjusted EBITDA / Revenue, min. Adjusted EBITDA = EBITDA plus the
    one-off items parse_ebitda_addbacks already turned into "ebitda_addback"
    reclassifications in `adj` (materiality floor applied there, not here)."""
    def actual_of(rs, a):
        rev = category_total(rs, "revenue", a)
        adj_ebitda = ebitda(rs, a) + category_total(rs, "ebitda_addback", a)
        return adj_ebitda / rev if rev else Decimal(0)
    raw = actual_of(rows, adj)
    status = verdict(raw, floor, "min")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, floor, "min")}


def solve_group_capex_ebitda(rows, adj, cap):
    """P5: Group capex / Borrower EBITDA, max. Borrower EBITDA is computable
    (Revenue - Opex, stated inline). Group capex is explicitly the *consolidated
    parent's* figure and is never disclosed anywhere in the open set - not the
    agreement, not the KYC dossier, not the audit notes, not any other document
    tied to this account or mentioning the borrower by name (checked). Returns
    None rather than invent a number: the caller keeps the prior for this cell.
    See the final report's "could not solve" list."""
    return None


def solve_tax_utilities_ebitda(rows, adj, cap):
    """P7: (taxes + utility/communal charges) / EBITDA, max."""
    def actual_of(rs, a):
        e = ebitda(rs, a)
        burden = category_total(rs, "tax", a) + category_total(rs, "utilities", a)
        return burden / e if e else Decimal(0)
    raw = actual_of(rows, adj)
    status = verdict(raw, cap, "max")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, cap, "max")}


def solve_personnel_obligations(rows, adj, cap):
    """P8: total personnel obligations (a dollar amount, not a ratio), max.
    = payroll-tagged outflows (corrected where a treasury memo or audit note
    supplies a missing ledger amount) + a disclosed-but-unposted severance
    liability (`adj.disclosed` - no txn_id, so it can never itself be
    evidence, but it is still part of `actual`)."""
    def actual_of(rs, a):
        return category_total(rs, "payroll", a) + a.disclosed
    raw = actual_of(rows, adj)
    status = verdict(raw, cap, "max")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, cap, "max")}


def solve_subsidiary_transfer_share(rows, adj, pledges, threshold, cap):
    """P9: (capital assets transferred to Unrestricted subsidiaries) / (total
    capex), max. "Unrestricted" comes from the KYC pledge-coverage table
    (parse_subsidiary_status), not the ownership table - a subsidiary is
    Unrestricted when its pledged-asset share is BELOW `threshold`. Transfer
    transactions are themselves part of the capex base (confirmed by their own
    documented original classification, not assumed): a share-of-capex ratio
    only makes sense if the numerator is part of the denominator."""
    npledges = {norm_name(k): v for k, v in pledges.items()}

    def unrestricted(counterparty: str) -> bool:
        pct = npledges.get(norm_name(counterparty))
        return pct is not None and pct < threshold

    transfers = [r for r in rows if r["txn_id"] not in adj.excluded
                 and SUBSIDIARY_TRANSFER.search(r["description"])]
    base_capex = category_total(rows, "capex", adj)

    def ratio(candidate_pool):
        # denom is recomputed from candidate_pool, not closed over the full
        # transfers list: a transfer is part of the capex base (module
        # docstring above), so the evidence flip-test - which drops one
        # candidate at a time - must drop it from the denominator too, not
        # just the numerator. With a fixed denom the test silently favours
        # whichever transfer happens not to be unrestricted.
        denom = base_capex + outflow(candidate_pool)
        num = outflow([r for r in candidate_pool if unrestricted(r["counterparty"])])
        return num / denom if denom else Decimal(0)

    raw = ratio(transfers)
    status = verdict(raw, cap, "max")
    recompute = lambda keep: verdict(ratio(keep), cap, "max")
    # Every transfer's inclusion rests on the KYC pledge-status judgment, so
    # all of them (Restricted or not) are candidates - same discipline as
    # related-party payments in solve_related_party.
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_for(transfers, recompute, status)}


def solve_insurance_cover(rows, adj, floor):
    """P10: insurance premiums / (lease + utility/communal charges), min."""
    def actual_of(rs, a):
        denom = category_total(rs, "lease", a) + category_total(rs, "utilities", a)
        return category_total(rs, "insurance", a) / denom if denom else Decimal(0)
    raw = actual_of(rows, adj)
    status = verdict(raw, floor, "min")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, floor, "min")}


def solve_interest_cover(rows, adj, floor):
    """B1: EBITDA / interest expense, min."""
    def actual_of(rs, a):
        interest = category_total(rs, "interest", a)
        return ebitda(rs, a) / interest if interest else Decimal(0)
    raw = actual_of(rows, adj)
    status = verdict(raw, floor, "min")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, floor, "min")}


def solve_q4_revenue(rows, adj, floor, q_start="2025-10-01", q_end="2025-12-31"):
    """B4: revenue for one quarter, not the year - COVENANTS.md's landmine.
    Filtered by date first; a description saying "fourth quarter" is not
    reliable on its own (B4 has the same-shaped row for Q1-Q3 too)."""
    q_rows = [r for r in rows if q_start <= r["date"] <= q_end]

    def actual_of(rs, a):
        return category_total(rs, "revenue", a)
    raw = actual_of(q_rows, adj)
    status = verdict(raw, floor, "min")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(q_rows, adj, actual_of, floor, "min")}


# ---------------------------------------------------------------------------
# Three clause 6.2 "landmines" (COVENANTS.md) that fall outside patterns A-D
# for the same reason clause 6.1 does - a genuinely different metric - but
# turn out to need no new categories at all, only a different combination of
# payroll/utilities/tax/revenue than clause 6.1 already uses elsewhere.
# ---------------------------------------------------------------------------


def solve_revenue_coverage(rows, adj, floor):
    """P6 6.2: Revenue / (payroll + utilities), min."""
    def actual_of(rs, a):
        burden = category_total(rs, "payroll", a) + category_total(rs, "utilities", a)
        return category_total(rs, "revenue", a) / burden if burden else Decimal(0)
    raw = actual_of(rows, adj)
    status = verdict(raw, floor, "min")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, floor, "min")}


def solve_revenue_minus_larger_overhead(rows, adj, floor):
    """P10 6.2: Revenue minus the LARGER of payroll or taxes, min - "the
    smaller of the two is not counted" per the clause's own wording, so this
    is a max() of the two lines, never their sum."""
    def actual_of(rs, a):
        overhead = max(category_total(rs, "payroll", a), category_total(rs, "tax", a))
        return category_total(rs, "revenue", a) - overhead
    raw = actual_of(rows, adj)
    status = verdict(raw, floor, "min")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, floor, "min")}


def solve_max_overhead_line(rows, adj, cap):
    """B1 6.2: the LARGER of payroll or utilities must not exceed `cap` -
    "checked individually, not in aggregate; their sum is not this covenant's
    metric" per the clause's own wording."""
    def actual_of(rs, a):
        return max(category_total(rs, "payroll", a), category_total(rs, "utilities", a))
    raw = actual_of(rows, adj)
    status = verdict(raw, cap, "max")
    return {"status": status, "actual": float(q2(raw)),
            "evidence_txn_id": evidence_from_judgment(rows, adj, actual_of, cap, "max")}


def demo() -> None:
    """Smallest checks that fail if the clause-6.1 machinery breaks: the three
    adjustment-sentence regexes, reclassification-aware category totals, the
    two table parsers (EBITDA add-backs, subsidiary pledges), and one full
    solver flipping status when a judgment is undone - the exact shape
    TRAPS.md's evidence rule describes."""
    rows = [
        {"txn_id": "TXN-X-0001", "amount": "-100.00", "description": "Consulting services", "counterparty": "Acme"},
        {"txn_id": "TXN-X-0002", "amount": "-50.00", "description": "operating costs", "counterparty": "Beta"},
    ]
    adj = Adjustments(reclass={"TXN-X-0001": "opex"})
    assert category_total(rows, "opex", adj) == Decimal("150.00")
    assert category_total(rows, "opex", Adjustments()) == Decimal("50.00")

    notes = (
        "Сумма в размере $100.00, выплаченная контрагенту Acme, первоначально "
        "учтённая как Консультационные услуги, переклассифицирована для целей "
        "соблюдения ковенантов как Операционные расходы. "
        "Операция TXN-X-0003 (Gamma): сумма не отражена в выгрузке реестра; "
        "фактическая сумма операции составляет $75.00 (расход). "
        "Операция TXN-X-0004, датированная 2025-11-01, исключена из "
        "ковенантного периода 2025 года."
    )
    adj2 = parse_adjustments([notes], rows)
    assert adj2.reclass == {"TXN-X-0001": "opex"}
    assert adj2.corrections == {"TXN-X-0003": Decimal("-75.00")}
    assert adj2.excluded == {"TXN-X-0004"}
    # A considered-and-rejected sentence must NOT be mistaken for an accepted one.
    rejected = ("Операция TXN-X-0005, первоначально учтённая как Операционные "
                "расходы ($10.00), рассматривалась на предмет возможной "
                "переклассификации как Страховые премии; первоначальная "
                "классификация сохраняется.")
    assert parse_adjustments([rejected], rows).reclass == {}

    global EUR_USD_RATE
    saved_rate = EUR_USD_RATE
    try:
        EUR_USD_RATE = None
        assert amount({"amount": "100.00", "currency": "EUR"}) == Decimal("100.00")
        EUR_USD_RATE = Decimal("1.5")
        assert amount({"amount": "100.00", "currency": "EUR"}) == Decimal("150.00")
        assert amount({"amount": "100.00", "currency": "USD"}) == Decimal("100.00")
    finally:
        EUR_USD_RATE = saved_rate

    addback_text = ("Item | Counterparty | Amount\n"
                     "Thing one | «Alpha LLP» | $500.00\n"
                     "Thing two | «Beta LLC» | $100.00\n"
                     "\nстатьи в сумме "
                     "не менее $300.00; меньшие "
                     "не прибавляются.\n")
    ab_rows = [
        {"txn_id": "TXN-Z-0001", "amount": "-500.00", "description": "thing one", "counterparty": "Alpha LLP"},
        {"txn_id": "TXN-Z-0002", "amount": "-100.00", "description": "thing two", "counterparty": "Beta LLC"},
    ]
    assert parse_ebitda_addbacks(addback_text, ab_rows) == {"TXN-Z-0001": "ebitda_addback"}

    sub_text = ("Доля активов в залоге "
                "Alpha LLP: 60.0% Beta LLC: 20.0% "
                "Дочерние организации, "
                "у которых доля активов "
                "в залоге ниже 50.0%, находятся "
                "вне периметра обеспечения")
    thr, pledges = parse_subsidiary_status(sub_text)
    assert thr == Decimal("50.0")
    assert pledges == {"Alpha LLP": Decimal("60.0"), "Beta LLC": Decimal("20.0")}

    # End to end: undoing a reclassification flips capital intensity from
    # COMPLIANT to BREACH, so that reclassification IS the evidence.
    ci_rows = [
        {"txn_id": "TXN-Y-0001", "amount": "-50.00", "description": "purchase of crane", "counterparty": "C"},
        {"txn_id": "TXN-Y-0002", "amount": "-80.00", "description": "operating costs", "counterparty": "D"},
        {"txn_id": "TXN-Y-0003", "amount": "-20.00", "description": "consulting", "counterparty": "E"},
    ]
    got = solve_capital_intensity(ci_rows, Adjustments(reclass={"TXN-Y-0003": "opex"}), Decimal("0.55"))
    assert got == {"status": "COMPLIANT", "actual": 0.5, "evidence_txn_id": "TXN-Y-0003"}, got

    # Pattern D (solve_max_capex) must apply a capex reclassification, not just
    # the description regex - every Pattern-D clause's own wording says so.
    capex_rows = [
        {"txn_id": "TXN-Z-0001", "amount": "-50.00", "description": "purchase of crane", "counterparty": "C"},
        {"txn_id": "TXN-Z-0002", "amount": "-30.00", "description": "consulting", "counterparty": "D"},
    ]
    got = solve_max_capex(capex_rows, Adjustments(reclass={"TXN-Z-0002": "capex"}), Decimal("60"))
    assert got == {"status": "BREACH", "actual": 80.0, "evidence_txn_id": "TXN-Z-0002"}, got

    # Pattern C (solve_min_revenue) must be equally reclassification-aware.
    rev_rows = [
        {"txn_id": "TXN-W-0001", "amount": "40.00", "description": "sales settlement", "counterparty": "C"},
        {"txn_id": "TXN-W-0002", "amount": "20.00", "description": "rebate", "counterparty": "D"},
    ]
    got = solve_min_revenue(rev_rows, Adjustments(reclass={"TXN-W-0002": "revenue"}), Decimal("50"))
    assert got == {"status": "COMPLIANT", "actual": 60.0, "evidence_txn_id": "TXN-W-0002"}, got

    # solve_subsidiary_transfer_share: the denominator must be recomputed per
    # candidate, not closed over the full transfer list - otherwise dropping a
    # RESTRICTED transfer (which never feeds the numerator) can never look like
    # evidence, even when it dominates the denominator enough to flip the ratio.
    sub_rows = [
        {"txn_id": "TXN-V-0001", "amount": "-100.00",
         "description": "Transfer of kiln parts to subsidiary", "counterparty": "Sub A"},
        {"txn_id": "TXN-V-0002", "amount": "-20.00",
         "description": "Transfer of spare parts to subsidiary", "counterparty": "Sub B"},
    ]
    got = solve_subsidiary_transfer_share(
        sub_rows, Adjustments(), {"Sub A": Decimal("80.0"), "Sub B": Decimal("10.0")},
        Decimal("50.0"), Decimal("0.25"))
    assert got == {"status": "COMPLIANT", "actual": 0.17, "evidence_txn_id": "TXN-V-0001"}, got

    print("solve ok")


if __name__ == "__main__":
    demo()
