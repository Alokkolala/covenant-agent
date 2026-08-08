#!/usr/bin/env python
"""Merge per-borrower agent answers into a submittable file, and flag what to check.

    python tools/collect.py --packets packets --template <dir>/submission_template.json
    python tools/collect.py --self-check

Order matters and is the whole point: the parachute fills every cell first, then a
packet's answer overwrites only the cells it actually proves. A packet that is
missing, unparseable, or malformed leaves its cells on the prior instead of taking
the submission down. An empty cell and a wrong cell score the same, so nothing is
ever left blank.

Cells whose `actual` sits far from the covenant's own threshold are flagged, not
corrected. Measured on the open set: every ground-truth `actual` lands within
0.61x-1.47x of its threshold, so a value outside 0.5x-2.0x is almost certainly a
misread metric. It catches gross blowups, NOT small drift - it missed a 12% error
on a hand-checked cell. A flag is a hand-check list, never an edit.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fallback  # noqa: E402

BAND = (0.5, 2.0)
STATUSES = {"COMPLIANT", "BREACH"}


def validate(cell: dict) -> str | None:
    """Why this cell is not submittable, or None if it is."""
    if cell.get("status") not in STATUSES:
        return f"status {cell.get('status')!r} is not COMPLIANT/BREACH"
    a = cell.get("actual")
    if isinstance(a, bool) or not isinstance(a, (int, float)):
        return f"actual {a!r} is not a number"
    if a < 0:
        return f"actual {a} is negative"
    ev = cell.get("evidence_txn_id")
    if ev is not None and not isinstance(ev, str):
        return f"evidence_txn_id {ev!r} is neither a string nor null"
    return None


def collect(packets: Path, template: Path, out: Path) -> int:
    payload = fallback.build(json.loads(template.read_text(encoding="utf-8")))
    cells = {s: dict(c) for s, c in payload["answers"].items()}
    taken, kept, flags, problems = 0, 0, [], []

    for scen in sorted(cells):
        f = packets / f"{scen.lower()}packet" / "answer.json"
        if not f.exists():
            kept += len(cells[scen])
            continue
        try:
            got = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            problems.append(f"{scen}: unreadable answer.json ({e}); all cells on priors")
            kept += len(cells[scen])
            continue
        # Agents drift between the flat shape they were asked for and the full
        # submission wrapper. Both are unambiguous, and silently dropping a
        # borrower over an envelope would be the most expensive kind of quiet
        # failure. Accept either.
        if isinstance(got.get("answers"), dict):
            inner = got["answers"]
            got = inner.get(scen) or (next(iter(inner.values())) if len(inner) == 1 else {})
            if not got:
                problems.append(f"{scen}: wrapped answer.json holds no cells for it; "
                                f"priors kept")
        for clause in cells[scen]:
            cell = got.get(clause)
            if not isinstance(cell, dict):
                problems.append(f"{scen}/{clause}: absent from answer.json; prior kept")
                kept += 1
                continue
            if why := validate(cell):
                problems.append(f"{scen}/{clause}: {why}; prior kept")
                kept += 1
                continue
            if cell.get("_computable") is False:
                # The agent computed a proxy, not the metric. Measured: a proxy
                # verdict scored 0 where the untouched prior scored 0.5.
                problems.append(f"{scen}/{clause}: agent reports the metric is not "
                                f"computable; prior kept")
                kept += 1
                continue
            thr = cell.get("_threshold")
            if isinstance(thr, (int, float)) and thr:
                r = abs(cell["actual"]) / abs(thr)
                if not BAND[0] <= r <= BAND[1]:
                    flags.append(f"{scen}/{clause}: actual/threshold = {r:.2f}x, outside "
                                 f"{BAND[0]}-{BAND[1]}x")
            # Underscore keys are working notes, never submitted.
            payload["answers"][scen][clause] = {
                k: v for k, v in cell.items() if not k.startswith("_")
            }
            taken += 1

    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)  # atomic

    total = taken + kept
    print(f"{out}: {taken}/{total} cells from agents, {kept} on priors")
    for p in problems:
        print(f"  PROBLEM  {p}")
    for f in flags:
        print(f"  FLAG     {f}")
    if flags:
        print("\nFlags are a hand-check list, not an error. The band catches gross "
              "blowups only - a 12% drift passes it.")
    return 0


def demo() -> None:
    import tempfile
    tpl = {"team": "", "contact_email": "", "model": "",
           "answers": {"X1": {c: {"status": None, "actual": None, "evidence_txn_id": None}
                              for c in ("6.1", "6.2", "6.3")}}}
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "x1packet").mkdir()
        (d / "x1packet" / "answer.json").write_text(json.dumps({
            "6.1": {"status": "BREACH", "actual": 1.71, "evidence_txn_id": None,
                    "_threshold": 1.70},                       # in band, submitted
            "6.2": {"status": "COMPLIANT", "actual": 50.0, "evidence_txn_id": None,
                    "_threshold": 1.0},                        # 50x -> flagged, still taken
            "6.3": {"status": "MAYBE", "actual": 5.0, "evidence_txn_id": None},
        }), encoding="utf-8")                                  # bad status -> prior kept
        (d / "x2packet").mkdir()
        (d / "x2packet" / "answer.json").write_text(json.dumps({
            # Well-formed and plausible, but the agent says it is a proxy.
            "6.1": {"status": "COMPLIANT", "actual": 0.74, "evidence_txn_id": None,
                    "_threshold": 9.0, "_computable": False},
        }), encoding="utf-8")
        tpl["answers"]["X2"] = {"6.1": {"status": None, "actual": None,
                                        "evidence_txn_id": None}}
        # An agent that answered in the full submission envelope instead of the
        # flat shape. Losing a borrower to a wrapper is not an acceptable failure.
        (d / "x3packet").mkdir()
        (d / "x3packet" / "answer.json").write_text(json.dumps({
            "team": "solo", "model": "claude-opus-5",
            "answers": {"X3": {"6.1": {"status": "BREACH", "actual": 9.99,
                                       "evidence_txn_id": None}}},
        }), encoding="utf-8")
        tpl["answers"]["X3"] = {"6.1": {"status": None, "actual": None,
                                        "evidence_txn_id": None}}
        t = d / "tpl.json"
        t.write_text(json.dumps(tpl), encoding="utf-8")
        out = d / "sub.json"
        collect(d, t, out)
        answers = json.loads(out.read_text(encoding="utf-8"))["answers"]
        got, proxy, wrapped = answers["X1"], answers["X2"]["6.1"], answers["X3"]["6.1"]

    assert wrapped["actual"] == 9.99, "a wrapped answer.json must still be collected"
    assert proxy["actual"] != 0.74, "a self-declared proxy must not reach the submission"
    assert proxy["status"] in STATUSES, "the prior must still fill the cell"
    assert got["6.1"]["actual"] == 1.71
    assert "_threshold" not in got["6.1"], "working note leaked into the submission"
    assert got["6.2"]["actual"] == 50.0, "a flag must not suppress the answer"
    assert got["6.3"]["status"] in STATUSES, "invalid cell must fall back to a prior"
    assert all(c["status"] in STATUSES and isinstance(c["actual"], (int, float))
               for c in got.values()), "every cell must be submittable"
    print("collect self-check ok")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        demo()
        raise SystemExit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("--packets", default=str(Path.home() / "halyk-packets"),
                    help="where tools/packet.py wrote the per-borrower packets")
    ap.add_argument("--data", default=str(ROOT / "agentic-bank-public"),
                    help="the dataset root; the template is located inside it by shape")
    ap.add_argument("--template", help="override if the template is somewhere else")
    ap.add_argument("--out", default=str(ROOT / "submission_agents.json"))
    a = ap.parse_args()
    if a.template:
        template = Path(a.template)
    else:
        sys.path.insert(0, str(Path(__file__).parent))
        from packet import find  # same shape-based lookup the packets were built with
        template = find(Path(a.data))["template"]
    raise SystemExit(collect(Path(a.packets), template, Path(a.out)))
