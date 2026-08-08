"""Work out what each opaquely-named document is, and whose it is.

File names are hashes and reveal nothing, so classification is by content only.
133 of 200 PDFs in the open set are pure noise (HR policy, marketing, IT incident
reports) and every borrower has a superseded twin of its credit agreement.

The invariants here HALT the run rather than filter. Computing a covenant against
a superseded threshold yields a confident wrong answer, which scores zero and looks
like success.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import ingest

# Markers are matched against whitespace-collapsed text: PDF headers wrap mid-phrase
# ("ЗАЁМ\n№ ACC-7801"), so anything matched on raw output silently misses.
SUPERSEDED = "НЕДЕЙСТВУЮЩАЯ"
HEAD = 900  # chars of normalised head used for classification

MARKERS = [
    # (kind, needles searched in the head) - order matters, first hit wins
    ("agreement_dead", (SUPERSEDED,)),
    ("agreement", ("ДОГОВОР БАНКОВСКОГО ЗАЙМА",)),
    ("kyc", ("Знай своего клиента", "(KYC)", "НАДЛЕЖАЩАЯ ПРОВЕРКА КЛИЕНТА")),
    ("audit_notes", ("Примечания к финансовой отчётности", "АУДИТОРСКОЕ ДЕЛО")),
    ("cert_draft", ("ПРОМЕЖУТОЧНАЯ ВЕДОМОСТЬ",)),
    ("cert_final", ("Отчёт о выполнении",)),
    # Treasury memos: same shape as audit_notes' "amounts missing from the ledger
    # extract" disclosures, but filed by treasury instead of the auditor (P7's
    # covenant 6.1 is unreachable without this - see TRAPS.md-style note in
    # 26acfab1e58b, previously misclassified as noise despite carrying an account_id).
    ("treasury_notes", ("Служебная записка казначейства", "Операционное управление казначейства")),
]
NEEDED = ("agreement", "kyc", "audit_notes")


def flatten(s: str) -> str:
    """Collapse every run of whitespace, including the newlines PDF text is full of."""
    return re.sub(r"\s+", " ", s or "").strip()


@dataclass
class Entry:
    doc_id: str
    path: str
    kind: str
    account: str | None
    pages: int
    readable: bool = True
    note: str = ""


@dataclass
class Index:
    entries: list[Entry] = field(default_factory=list)
    by_account: dict = field(default_factory=dict)   # acc -> {kind: [Entry]}
    unreadable: list = field(default_factory=list)   # scans etc - never silently dropped

    def get(self, account: str, kind: str) -> list[Entry]:
        return self.by_account.get(account, {}).get(kind, [])

    def one(self, account: str, kind: str) -> Entry | None:
        got = self.get(account, kind)
        return got[0] if len(got) == 1 else None


def classify(head: str) -> str:
    for kind, needles in MARKERS:
        if any(n in head for n in needles):
            return kind
    return "noise"


def build(documents_dir: str | Path) -> Index:
    idx = Index()
    for path in sorted(Path(documents_dir).iterdir()):
        if path.suffix.lower() != ".pdf":
            continue  # a .csv access log and a 4-byte Thumbs.db live here too
        doc = ingest.load_pdf(path)
        if not doc.ok:
            # A scan may still be load-bearing: in the open set the ONLY untyped
            # PDF is P6's KYC dossier, holding its 40% related-party threshold.
            e = Entry(doc.doc_id, str(path), "unreadable", None, len(doc.pages),
                      readable=False, note=doc.note)
            idx.entries.append(e)
            idx.unreadable.append(e)
            continue

        head = flatten(doc.text[:HEAD])
        acc = re.search(r"ACC-\d{4}", flatten(doc.text))
        e = Entry(doc.doc_id, str(path), classify(head), acc.group(0) if acc else None,
                  len(doc.pages))
        idx.entries.append(e)
        if e.account and e.kind != "noise":
            idx.by_account.setdefault(e.account, {}).setdefault(e.kind, []).append(e)
    return idx


def check(idx: Index, accounts: list[str]) -> list[str]:
    """Invariants. A non-empty result means STOP, not 'continue with warnings'."""
    problems = []
    for acc in accounts:
        live = idx.get(acc, "agreement")
        if len(live) != 1:
            problems.append(f"{acc}: expected exactly 1 live agreement, found {len(live)} "
                            f"{[e.doc_id for e in live]}")
        for e in live:
            if SUPERSEDED in flatten(ingest.load_pdf(Path(e.path)).text[:HEAD]):
                problems.append(f"{acc}: agreement {e.doc_id} is a SUPERSEDED edition")
    return problems


def report(idx: Index, accounts: list[str]) -> str:
    lines = [f"{len(idx.entries)} pdfs | "
             + " ".join(f"{k}={sum(1 for e in idx.entries if e.kind == k)}"
                        for k in ("agreement", "agreement_dead", "kyc", "audit_notes",
                                  "cert_draft", "cert_final", "noise", "unreadable"))]
    for acc in accounts:
        have = {k: len(idx.get(acc, k)) for k in NEEDED}
        missing = [k for k, n in have.items() if n == 0]
        lines.append(f"  {acc}: " + " ".join(f"{k}={n}" for k, n in have.items())
                     + (f"   MISSING {missing}" if missing else ""))
    if idx.unreadable:
        lines.append("  unreadable (needs OCR/vision, may be load-bearing): "
                     + ", ".join(f"{e.doc_id}({e.note})" for e in idx.unreadable))
    return "\n".join(lines)
