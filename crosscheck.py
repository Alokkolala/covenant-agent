#!/usr/bin/env python
"""Find suspect cells WITHOUT an answer key, by disagreement between two
independent derivations.

    python crosscheck.py submission.json other_submission.json [--doubt cells.txt]

On Aug 9 there is no key, so the only available error signal is inconsistency.
Two things were measured on the open set, and neither is sufficient alone:

  * two independent derivations disagreeing caught 2 of the pipeline's 4 errors,
    with 3 false alarms among 5 flags
  * an agent's own "I am not confident here" caught every cell it was wrong
    about - but it was also wrong on cells it felt sure of

Together they covered 3 of 4. The one they missed is the cell no approach has
ever explained, which is itself the honest answer: some errors are invisible
until the mechanism is understood.

So this does NOT rank answers by confidence. It produces a hand-check list, and
the only claim made for it is that checking those cells is a better use of the
last hour than checking cells at random.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

TOL = 0.05  # same relative band the official scoring uses for `actual`


def cells(sub: dict) -> dict:
    out = {}
    for scen, clauses in sub.get("answers", {}).items():
        for clause, cell in clauses.items():
            out[f"{scen}/{clause}"] = cell
    return out


def compare(a: dict, b: dict, doubted: set[str]) -> list[tuple[str, str, str]]:
    flags = []
    A, B = cells(a), cells(b)
    for name in sorted(set(A) | set(B)):
        x, y = A.get(name), B.get(name)
        if x is None or y is None:
            flags.append((name, "MISSING", f"present in only one submission"))
            continue
        if x.get("status") != y.get("status"):
            # The expensive kind: a wrong status zeroes the whole cell.
            flags.append((name, "STATUS", f"{x.get('status')} vs {y.get('status')}"))
            continue
        xa, ya = x.get("actual"), y.get("actual")
        if isinstance(xa, (int, float)) and isinstance(ya, (int, float)) and ya:
            err = abs(xa - ya) / abs(ya)
            if err > TOL:
                flags.append((name, "ACTUAL", f"{xa} vs {ya} ({err * 100:.1f}% apart)"))
                continue
        if x.get("evidence_txn_id") != y.get("evidence_txn_id"):
            flags.append((name, "EVIDENCE",
                          f"{x.get('evidence_txn_id')} vs {y.get('evidence_txn_id')}"))
            continue
        if name in doubted:
            flags.append((name, "DOUBT", "both agree, but flagged as low-confidence"))
    return flags


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--doubt", help="file listing cells (SCEN/CLAUSE per line) the "
                                    "solver was not confident about")
    args = ap.parse_args()

    a = json.loads(Path(args.a).read_text(encoding="utf-8"))
    b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    doubted = set()
    if args.doubt and Path(args.doubt).exists():
        doubted = {ln.strip() for ln in Path(args.doubt).read_text(encoding="utf-8").splitlines()
                   if ln.strip() and not ln.startswith("#")}

    flags = compare(a, b, doubted)
    total = len(cells(a))
    for name, kind, detail in flags:
        print(f"[{kind:<8}] {name:<10} {detail}")
    print(f"\n{len(flags)} of {total} cells need a human look "
          f"({100 * len(flags) / total if total else 0:.0f}%)")
    print("STATUS disagreements first: a wrong status zeroes the entire cell, so "
          "those are worth more than a wrong number.")
    return 0


def demo() -> None:
    a = {"answers": {"P1": {"6.1": {"status": "BREACH", "actual": 100.0, "evidence_txn_id": None}}}}
    b = {"answers": {"P1": {"6.1": {"status": "COMPLIANT", "actual": 100.0, "evidence_txn_id": None}}}}
    assert compare(a, b, set())[0][1] == "STATUS"
    c = {"answers": {"P1": {"6.1": {"status": "BREACH", "actual": 130.0, "evidence_txn_id": None}}}}
    assert compare(a, c, set())[0][1] == "ACTUAL"
    d = {"answers": {"P1": {"6.1": {"status": "BREACH", "actual": 100.5, "evidence_txn_id": None}}}}
    assert compare(a, d, set()) == []          # inside the 5% band, not a flag
    assert compare(a, d, {"P1/6.1"})[0][1] == "DOUBT"
    print("crosscheck self-check ok")


if __name__ == "__main__":
    import sys
    if "--self-check" in sys.argv:
        demo()
    else:
        raise SystemExit(main())
