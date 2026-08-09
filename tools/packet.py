#!/usr/bin/env python
"""Build a per-borrower packet: the case, the ledger, that borrower's documents.

    python tools/packet.py P7 [--data agentic-bank-public] [--out <dir>]

One agent, one borrower. Measured: handed all 12 borrowers and 200 documents an
agent made 10 errors; handed one borrower and its own folder, 0-2.

The packet carries EVERY document naming this borrower's account, whatever the
index calls it - a correction has already been found in a treasury memo the
index classified as noise. The classifier decides what to SHOW, not what to USE.

Never ships: the answer key, the pipeline, or any notes file. The agent must
derive, not read off.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline import index, ingest, solve  # noqa: E402

BANNED = ("ground_truth.json", "submission.json")


def find(data: Path) -> dict[str, Path]:
    """Locate the dataset's parts by shape, not by name.

    Every filename here was hardcoded until it was pointed out that the next
    archive names things its own way, and a KeyError at minute five of a
    three-hour window is not recoverable. Nothing is matched on an exact name:
    the ledger is the csv carrying a `txn_id` column, the template is the json
    whose cells have a `status` field, documents is the directory with the pdfs.
    """
    found: dict[str, Path] = {}

    for f in sorted(data.rglob("*.csv")):
        try:
            with f.open(encoding="utf-8-sig") as stream:
                head = next(csv.DictReader(stream), None)
        except (UnicodeDecodeError, csv.Error):
            continue
        if head and "txn_id" in head and "account_id" in head:
            found["ledger"] = f
            break

    for f in sorted(data.rglob("*.json")):
        if f.name in BANNED:
            continue
        try:
            blob = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        cells = blob.get("answers") if isinstance(blob, dict) else None
        if isinstance(cells, dict) and any(
                isinstance(c, dict) and "status" in c
                for clauses in cells.values() if isinstance(clauses, dict)
                for c in clauses.values()):
            found["template"] = f
            break

    dirs = {p.parent for p in data.rglob("*.pdf")}
    if dirs:
        found["documents"] = max(dirs, key=lambda d: len(list(d.glob("*.pdf"))))

    # Prefer the Russian edition where the archive ships several translations:
    # the documents are Russian, so a Russian brief keeps the vocabulary aligned.
    if case := sorted(data.rglob("CASE*.md")) or sorted(data.rglob("*.md")):
        found["case"] = next((c for c in case if ".ru." in c.name), case[0])

    missing = {"ledger", "template", "documents"} - set(found)
    if missing:
        raise SystemExit(f"cannot find {sorted(missing)} under {data} - "
                         f"saw {sorted(p.name for p in data.iterdir())}")
    return found


def account_of(scenario: str, data: Path) -> str:
    with find(data)["ledger"].open(encoding="utf-8-sig") as ledger:
        accs = {r["account_id"] for r in csv.DictReader(ledger)
                if r["txn_id"].split("-")[1] == scenario}
    if len(accs) != 1:
        raise SystemExit(f"{scenario}: expected exactly one account, found {sorted(accs)}")
    return accs.pop()


def stem(description: str) -> str:
    """A description's word family, for telling a planted row from generated filler.

    Group by WORD FAMILY, not by the text before the dash. Splitting on the
    separator was the first attempt and it lies: a filler row with no suffix to
    strip keeps its whole description as its "stem" and so looks unique. That
    false positive was caught in the wild - "Travel insurance for staff 2025"
    scored seen=1 while six siblings sat on unrelated accounts, and believing it
    would have moved a cell's `actual` by 16%.
    """
    head = re.split(r"\s+[—–-]\s+", description)[0]
    return " ".join(re.findall(r"[a-zа-яё]+", head.lower())[:4])


def seen_counts(every: list[dict]) -> dict[str, int]:
    """stem -> how many DISTINCT accounts in the whole file share that wording.

    Count accounts, not rows. Filler templates are reused across hundreds of
    unrelated accounts; a planted row sits on exactly one - and one borrower
    legitimately has four rows sharing a wording because its covenant measures
    four quarters, which a row count would mislabel as filler.
    """
    spread: dict[str, set] = {}
    for r in every:
        spread.setdefault(stem(r["description"]), set()).add(r["account_id"])
    return {k: len(v) for k, v in spread.items()}


def brief(acc: str, data: Path) -> str:
    """The borrower's rows, pre-grouped by the category markers.

    Split along the line the measurements drew: over eighteen hand-scored cells the
    code never mis-categorised a row and an agent did so three times, always by
    reading the category semantically. So the code proposes the grouping. It is a
    proposal, not an answer - the markers are this generator's vocabulary and the
    next dataset may word a category differently, which is why every row is listed
    underneath, uncategorised ones included.
    """
    from decimal import Decimal, InvalidOperation

    with find(data)["ledger"].open(encoding="utf-8-sig") as ledger:
        every = list(csv.DictReader(ledger))
    rows = [r for r in every if r["account_id"] == acc]

    # How often this description's wording recurs across the WHOLE file. Filler
    # templates are reused on hundreds of unrelated accounts; the planted rows are
    # near-unique. Reported per row because the markers alone over-collect badly on
    # payroll, lease, insurance and interest - categories where the generator's
    # filler happens to share the marker's vocabulary.
    counts = seen_counts(every)
    seen = lambda r: counts[stem(r["description"])]  # noqa: E731
    out = [f"# Ledger brief — {acc}", "",
           f"{len(rows)} rows on this account, out of the whole file.", "",
           "## Category totals proposed by the markers", "",
           "These come from the wording of each description, never from what the item is "
           "in accounting terms. **Verify them against the descriptions below before "
           "using them** — and read the covenant first: it may define a category "
           "differently, or need one that is not here.", "",
           "`seen=N` is how many **distinct accounts** in the whole file share that "
           "description's wording. **`seen=1` means the wording exists only on this "
           "account — a planted row. A high count is a filler template reused across "
           "unrelated borrowers.** Counting accounts rather than rows on purpose: one "
           "borrower carries four rows sharing a wording because its covenant measures "
           "four quarters, and a row count would call them filler.", "",
           "Two things `seen` does **not** tell you, both measured:", "",
           "- `seen=1` is necessary, not sufficient. Check the counterparty too: a "
           "plausible regional company name marks a planted row; an invented generic one, "
           "often with a parenthetical site, marks filler.",
           "- `seen` high does not mean ignore. A planted row can wear a filler "
           "description and be designed through its **counterparty** — related-party "
           "payments do this routinely. Screening those out by description returns zero.", "",
           "The markers are reliable where `seen=1` and over-collect badly where they are "
           "not: `payroll`, `lease`, `insurance` and `interest` sweep up filler whose "
           "wording happens to match. A total built mostly from high-`seen` rows is almost "
           "certainly not the quantity the covenant means.", ""]

    claimed: set[str] = set()
    for name in solve.CATEGORIES:
        hits = solve.category_rows(rows, name, solve.Adjustments())
        if not hits:
            continue
        claimed |= {h["txn_id"] for h in hits}
        total = solve.category_total(rows, name, solve.Adjustments())
        # Warn only where over-collection is the likely story: many rows, few of
        # them near-unique. A single row with a repeated stem is just as often the
        # real one - P7's utilities line is exactly that - and flagging it would
        # push the reader off a correct total.
        planted = sum(1 for h in hits if seen(h) == 1)
        note = (f"   ⚠ {len(hits)} rows but only {planted} near-unique — this total is "
                f"probably sweeping up filler"
                if len(hits) > 3 and planted * 2 < len(hits) else "")
        out.append(f"### {name} = {total:,.2f}{note}")
        for h in hits:
            out.append(f"- `{h['txn_id']}` seen={seen(h):<3} {h['amount']:>16} "
                       f"{h['currency']}  {h['description']}  · {h['counterparty']}")
        out.append("")

    flags = []
    if blank := [r["txn_id"] for r in rows if not r["amount"].strip()]:
        flags.append(f"**Blank `amount`**: {', '.join(blank)}. The real figure is disclosed "
                     f"in one of the documents; the clause that needs it often names where.")
    if fx := sorted({r["currency"] for r in rows} - {"USD"}):
        eur = [r["txn_id"] for r in rows if r["currency"] != "USD"]
        flags.append(f"**Non-USD rows** ({', '.join(fx)}): {', '.join(eur)}. If one falls in "
                     f"a category a covenant uses, the rate must be derived from a "
                     f"settlement pair disclosed in the documents.")
    if flags:
        out += ["## Flags", ""] + [f"- {f}" for f in flags] + [""]

    out += ["## Every row, in full", "",
            "Rows no marker claimed are marked `—`; that is not a verdict, only that the "
            "standard vocabulary did not match.", "",
            "| txn_id | date | amount | cur | seen | category | description | counterparty |",
            "|---|---|---:|---|---:|---|---|---|"]
    for r in sorted(rows, key=lambda r: r["txn_id"]):
        mark = "" if r["txn_id"] in claimed else "—"
        try:
            amt = f"{Decimal(r['amount']):,.2f}"
        except (InvalidOperation, ArithmeticError):
            amt = "**BLANK**"
        out.append(f"| `{r['txn_id']}` | {r['date']} | {amt} | {r['currency']} | "
                   f"{seen(r)} | {mark} | {r['description']} | {r['counterparty']} |")
    return "\n".join(out) + "\n"


def build(scenario: str, data: Path, out: Path, idx=None, force: bool = False) -> Path:
    acc = account_of(scenario, data)
    box = out / f"{scenario.lower()}packet"
    if (box / "answer.json").exists() and not force:
        # Rebuilding wipes the packet. Doing that under a running agent destroys
        # work already paid for - it has happened once. Pass --force to mean it.
        print(f"{scenario}: answer.json present, packet left alone (--force to rebuild)")
        return box
    if box.exists():
        shutil.rmtree(box)
    (box / "documents").mkdir(parents=True)

    parts = find(data)
    for key in ("case", "ledger", "template"):
        if src := parts.get(key):
            shutil.copy2(src, box / src.name)
    shutil.copy2(ROOT / "AGENT_PROTOCOL.md", box / "AGENT_PROTOCOL.md")

    if idx is None:
        idx = index.build(parts["documents"])
    # Every entry naming this account, regardless of the kind we assigned it.
    picked = [e for e in idx.entries if acc in ingest.load_pdf(Path(e.path)).text]
    # A document with no text layer names no account, so it belongs to no packet -
    # and it is exactly the kind that is load-bearing. On this dataset four such
    # files exist and they hold one borrower's entire KYC dossier and another's
    # EBITDA adjustments; dropping them costs those cells silently. They are cheap
    # to carry, so every packet gets all of them and the agent renders and reads
    # them to find out whose they are.
    blind = [e for e in idx.unreadable if e.account is None and e not in picked]
    for e in picked + blind:
        shutil.copy2(e.path, box / "documents" / Path(e.path).name)

    note = ""
    if blind:
        note = ("\n## Documents with no text layer\n\n"
                + "\n".join(f"- `{Path(e.path).name}`" for e in blind)
                + "\n\nThese carry no extractable text, so nothing could attribute them to "
                  "a borrower — they are in **every** packet. Render their pages "
                  "(`page.get_pixmap(dpi=200)`) and read them: one may be your KYC dossier "
                  "or your auditor's notes, and if it is, the covenants that depend on it "
                  "cannot be solved without it. If it names another borrower's account, "
                  "ignore it.\n")
    (box / "ledger_brief.md").write_text(brief(acc, data) + note, encoding="utf-8")

    for junk in list(box.rglob("__pycache__")) + [box / b for b in BANNED]:
        if junk.exists():
            shutil.rmtree(junk) if junk.is_dir() else junk.unlink()

    leaked = [p.name for p in box.rglob("*") if p.name in BANNED]
    if leaked:
        raise SystemExit(f"REFUSING: key leaked into packet: {leaked}")

    print(f"{scenario} -> {acc}   packet: {box}")
    print(f"  documents: {len(picked)}" + (f" (+{len(blind)} unreadable, in every packet)"
                                           if blind else ""))
    for e in sorted(picked, key=lambda e: e.kind):
        print(f"    {Path(e.path).name}   (our index says: {e.kind})")
    for e in blind:
        print(f"    {Path(e.path).name}   NO TEXT LAYER - render it, it may be yours")
    return box


def risk(scenario: str, data: Path) -> tuple[int, list[str]]:
    """How likely this borrower is to defeat the mechanical path - scored with no
    answer key, because on the day there is none. Higher means send an agent sooner.

    Every signal is something that has actually cost a cell: a clause the parser
    cannot classify falls back to a prior; a rasterised page hides a figure; a blank
    amount silently zeroes an aggregate; a foreign-currency row needs a rate derived
    from a document; a draft carries reclassifications that were never accepted.
    """
    import csv as _csv
    import pymupdf as fitz

    import run as engine

    acc = account_of(scenario, data)
    idx = index.build(find(data)["documents"])
    score, why = 0, []

    if agr := idx.one(acc, "agreement"):
        clauses = engine.parse_clauses(index.flatten(ingest.load_pdf(Path(agr.path)).text))
        blind = [c for c, s in clauses.items()
                 if s["kind"] == "?" and engine.structural_spec(s["text"]) is None]
        if blind:
            score += 4 * len(blind)
            why.append(f"{len(blind)} clause(s) the parser cannot read: {sorted(blind)}")
        if not clauses:
            score += 12
            why.append("no clauses parsed at all")
        # Covenant-level difficulty. Document mess is visible and cheap to score;
        # it is also NOT where the mechanical path actually fails. Measured: the
        # three borrowers it got wrong all name a quantity the corpus never
        # defines, and ranking on document signals alone put them 6th, 8th and 9th.
        for clause, spec in sorted(clauses.items()):
            low = spec["text"].lower()
            for pat, pts, note in (
                (("группы", "консолидир", "материнск"), 5,
                 "measured at a different reporting entity than the ledger"),
                (("ebitda", "эбитда"), 4, "needs EBITDA, defined nowhere in the corpus"),
                (("операционн",), 2, "needs operating expenses, defined nowhere"),
            ):
                if any(p in low for p in pat):
                    score += pts
                    why.append(f"{clause}: {note}")
    else:
        score += 12
        why.append("no unique live agreement")

    for e in idx.entries:
        if e.account != acc:
            continue
        with fitz.open(e.path) as doc:
            blanks = [i + 1 for i, p in enumerate(doc) if len(p.get_text().strip()) < 60]
        if blanks:
            score += 3
            why.append(f"{Path(e.path).name}: no text layer on page(s) {blanks}")
        if e.kind == "cert_draft":
            score += 2
            why.append(f"{Path(e.path).name}: draft certificate")

    with find(data)["ledger"].open(encoding="utf-8-sig") as ledger:
        rows = [r for r in _csv.DictReader(ledger) if r["account_id"] == acc]
    if blank := [r["txn_id"] for r in rows if not r["amount"].strip()]:
        score += 5
        why.append(f"blank amount: {blank}")
    if fx := {r["currency"] for r in rows} - {"USD"}:
        score += 2
        why.append(f"non-USD rows present: {sorted(fx)}")
    return score, why


def build_all(data: Path, out: Path, force: bool = False) -> int:
    """Every packet from one index. Building them one at a time re-parsed all the
    PDFs per borrower - twelve packets did not finish inside two minutes, which is
    time the window cannot spare."""
    parts = find(data)
    idx = index.build(parts["documents"])
    template = json.loads(parts["template"].read_text(encoding="utf-8"))
    ok, failed = [], []
    for scen in template["answers"]:
        try:
            build(scen, data, out, idx=idx, force=force)
            ok.append(scen)
        except SystemExit as e:
            failed.append(f"{scen}: {e}")
    print(f"\n{len(ok)} packets under {out}: {' '.join(ok)}")
    for f in failed:
        print(f"  FAILED {f}")
    return 1 if failed else 0


def triage(data: Path) -> int:
    template = json.loads(find(data)["template"].read_text(encoding="utf-8"))
    ranked = []
    for scen in template["answers"]:
        try:
            ranked.append((*risk(scen, data), scen))
        except SystemExit as e:
            ranked.append((99, [f"packet cannot even be built: {e}"], scen))
    ranked.sort(reverse=True)
    print("Send agents in this order - highest risk first.\n")
    for score, why, scen in ranked:
        print(f"  {scen:<4} risk {score:>3}")
        for w in why:
            print(f"        {w}")
    print("\nBorrowers with risk 0 are the ones the mechanical path already handles; "
          "give them agents last, or not at all if the window closes.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario",
                    help="scenario id, 'all' to build every packet, or 'triage' to rank them")
    ap.add_argument("--data", default=str(ROOT / "agentic-bank-public"))
    # Outside the repo by default: an agent given a packet inside it can reach
    # ground_truth.json. Every agent so far declined or plainly never looked -
    # two produced answers the key would have corrected - but a measurement that
    # rests on the subject's restraint is not a measurement.
    ap.add_argument("--out", default=str(Path.home() / "halyk-packets"))
    ap.add_argument("--force", action="store_true",
                    help="rebuild even where an answer.json already exists")
    a = ap.parse_args()
    if a.scenario.lower() == "triage":
        return triage(Path(a.data))
    if a.scenario.lower() == "all":
        return build_all(Path(a.data), Path(a.out), force=a.force)
    build(a.scenario.upper(), Path(a.data), Path(a.out), force=a.force)
    return 0


def demo() -> None:
    """Self-check: the packet must contain no answer key and no notes."""
    import tempfile

    import pymupdf as fitz

    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "custom-data"
        (data / "documents").mkdir(parents=True)
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "LOAN REFERENCE TELE-4471")
        doc.save(data / "documents" / "custom.pdf")
        doc.close()
        with (data / "ledger.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["txn_id", "date", "account_id", "counterparty", "description",
                        "amount", "currency"])
            w.writerow(["TXN-Z9-0001", "2025-01-01", "TELE-4471", "Acme", "Revenue",
                        "1.00", "USD"])
        (data / "template.json").write_text(json.dumps({"answers": {"Z9": {
            "9.4": {"status": None, "actual": None, "evidence_txn_id": None}}}}),
            encoding="utf-8")
        box = build("Z9", data, Path(tmp) / "custom-packets")
        assert (box / "documents" / "custom.pdf").exists(), \
            "documents must be matched against the ledger's exact account id"

        box = build("P7", ROOT / "agentic-bank-public", Path(tmp))
        names = {p.name for p in box.rglob("*") if p.is_file()}
        assert "ground_truth.json" not in names, "answer key leaked"
        assert not names & {"TRAPS.md", "COVENANTS.md", "GOAL.md", "PLAN.md", "READINESS.md"}
        assert "AGENT_PROTOCOL.md" in names and any(n.startswith("CASE") for n in names)
        assert sum(1 for p in (box / "documents").iterdir()) >= 3
        text = (box / "ledger_brief.md").read_text(encoding="utf-8")
        assert "Every row, in full" in text, "the brief must list rows, not only totals"
        assert "TXN-P7-0033" in text, "the borrower's rows must all appear"
        assert "BLANK" in text, "a blank amount must be visible, not silently zero"
    print("packet self-check ok")


if __name__ == "__main__":
    raise SystemExit(demo() if "--self-check" in sys.argv else main())
