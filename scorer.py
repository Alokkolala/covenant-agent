#!/usr/bin/env python
"""Score a submission against a ground-truth key. Implements CASE.ru section 4.

    python scorer.py submission.json agentic-bank-public/ground_truth.json
    python scorer.py --self-check

Deliberately knows nothing about covenants. Two JSON files in, one number out,
so it stays correct no matter how the reasoning pipeline changes.

    status           0.50  exact string match, else the WHOLE cell scores 0
    actual           0.30  0.30 * max(0, 1 - e/0.05),  e = |ours - key| / |key|
    evidence_txn_id  0.20  exact match; where the key is null, this 0.20 decays
                           with `actual` on the same scale

Cells are weighted by difficulty in the official scoring; those weights are not
published, so this reports the unweighted mean and says so.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

STATUSES = ("COMPLIANT", "BREACH")
W_STATUS, W_ACTUAL, W_EVIDENCE = 0.50, 0.30, 0.20
TOLERANCE = 0.05


def actual_factor(ours, key) -> float:
    """1.0 at exact match, decaying linearly to 0.0 at 5% relative error."""
    if isinstance(ours, bool) or not isinstance(ours, (int, float)):
        return 0.0  # missing or non-numeric
    if key is None:
        return 0.0
    if key == 0:
        return 1.0 if ours == 0 else 0.0
    e = abs(ours - key) / abs(key)
    return max(0.0, 1.0 - e / TOLERANCE)


def score_cell(got: dict | None, key: dict) -> tuple[float, dict]:
    detail = {"status": 0.0, "actual": 0.0, "evidence": 0.0, "note": ""}
    if not isinstance(got, dict):
        detail["note"] = "missing cell"
        return 0.0, detail

    if got.get("status") not in STATUSES:
        detail["note"] = f"status not exactly COMPLIANT/BREACH: {got.get('status')!r}"
        return 0.0, detail
    if got["status"] != key["status"]:
        detail["note"] = f"status {got['status']} != {key['status']} - whole cell zero"
        return 0.0, detail
    detail["status"] = W_STATUS

    factor = actual_factor(got.get("actual"), key.get("actual"))
    detail["actual"] = W_ACTUAL * factor

    if key.get("evidence_txn_id") is None:
        # Nothing to name here, so the evidence marks ride on `actual`'s accuracy.
        detail["evidence"] = W_EVIDENCE * factor
        detail["note"] = "evidence null in key - 0.20 rides on actual"
    else:
        hit = got.get("evidence_txn_id") == key["evidence_txn_id"]
        detail["evidence"] = W_EVIDENCE if hit else 0.0
        if not hit:
            detail["note"] = f"evidence {got.get('evidence_txn_id')!r} != {key['evidence_txn_id']!r}"

    return detail["status"] + detail["actual"] + detail["evidence"], detail


def load_key(obj: dict) -> dict[str, dict]:
    """ground_truth.json nests under scenarios/covenants; a submission does not."""
    if "scenarios" in obj:
        return {s: v["covenants"] for s, v in obj["scenarios"].items()}
    return obj.get("answers", obj)


def score(submission: dict, truth: dict) -> tuple[float, list]:
    key = load_key(truth)
    got = load_key(submission)
    rows = []
    for scenario in sorted(key):
        for clause in sorted(key[scenario]):
            cell, detail = score_cell(got.get(scenario, {}).get(clause), key[scenario][clause])
            rows.append((scenario, clause, cell, detail))
    total = sum(r[2] for r in rows) / len(rows) if rows else 0.0

    extra = [(s, c) for s in got for c in got.get(s, {})
             if s not in key or c not in key.get(s, {})]
    if extra:
        rows.append(("!", "extra", 0.0, {"note": f"keys not in template: {extra[:5]}"}))
    return total, rows


def report(submission_path, truth_path, verbose=True) -> float:
    sub = json.loads(Path(submission_path).read_text(encoding="utf-8"))
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    total, rows = score(sub, truth)

    if verbose:
        print(f"{'cell':<10} {'score':>6}  breakdown")
        for s, c, cell, d in rows:
            bits = f"st={d['status']:.2f} ac={d['actual']:.2f} ev={d['evidence']:.2f}"
            print(f"{s+'/'+c:<10} {cell:>6.3f}  {bits}  {d['note']}")
        n = len([r for r in rows if r[0] != "!"])
        exact = sum(1 for r in rows if r[2] > 0.999)
        st_ok = sum(1 for r in rows if r[3]["status"] > 0)
        print(f"\ncells {n} | status correct {st_ok}/{n} | full marks {exact}/{n}")
    print(f"SCORE {total:.4f}  (unweighted mean; official weights by difficulty are unpublished)")
    return total


def self_check() -> None:
    key = {"status": "BREACH", "actual": 100.0, "evidence_txn_id": None}
    assert score_cell({"status": "BREACH", "actual": 100.0, "evidence_txn_id": None}, key)[0] == 1.0
    # wrong status zeroes everything, however good the rest is
    assert score_cell({"status": "COMPLIANT", "actual": 100.0, "evidence_txn_id": None}, key)[0] == 0.0
    # 2.5% error -> half of BOTH actual and the evidence that rides on it
    s, _ = score_cell({"status": "BREACH", "actual": 102.5, "evidence_txn_id": None}, key)
    assert abs(s - (0.50 + 0.15 + 0.10)) < 1e-9, s
    # 5% error -> both drop to zero, status survives
    s, _ = score_cell({"status": "BREACH", "actual": 105.0, "evidence_txn_id": None}, key)
    assert abs(s - 0.50) < 1e-9, s
    # missing / non-numeric actual behaves like a total miss
    assert abs(score_cell({"status": "BREACH", "evidence_txn_id": None}, key)[0] - 0.50) < 1e-9
    assert abs(score_cell({"status": "BREACH", "actual": "100", "evidence_txn_id": None}, key)[0] - 0.50) < 1e-9

    ekey = {"status": "BREACH", "actual": 100.0, "evidence_txn_id": "TXN-1"}
    # named evidence is all-or-nothing and does NOT decay with actual
    assert score_cell({"status": "BREACH", "actual": 100.0, "evidence_txn_id": "TXN-1"}, ekey)[0] == 1.0
    s, _ = score_cell({"status": "BREACH", "actual": 100.0, "evidence_txn_id": "TXN-9"}, ekey)
    assert abs(s - 0.80) < 1e-9, s
    # guessing an id where the key names one still earns actual's marks
    s, _ = score_cell({"status": "BREACH", "actual": 105.0, "evidence_txn_id": "TXN-1"}, ekey)
    assert abs(s - 0.70) < 1e-9, s
    assert score_cell(None, key)[0] == 0.0
    assert actual_factor(0, 0) == 1.0 and actual_factor(1, 0) == 0.0
    print("scorer self-check ok")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        self_check()
    elif len(sys.argv) >= 3:
        report(sys.argv[1], sys.argv[2], verbose="-q" not in sys.argv)
    else:
        sys.exit(__doc__)
