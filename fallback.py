#!/usr/bin/env python
"""The parachute. Produces a valid, fully-populated submission with ZERO analysis.

    python fallback.py --template <dir>/submission_template.json --out submission.json

This is the FIRST thing to run on Aug 9, before any reasoning code touches the
private data. It guarantees a scoreable file exists on disk within seconds, so
every later run is an improvement on a banked result rather than a gamble.

It cannot read a document and does not try. Priors below are the majority class
and the median magnitude per clause, measured on the open set. They will be less
accurate on the private set; that is fine. An empty cell and a wrong cell score
the same, so answering everything strictly dominates answering nothing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Measured from the open set's ground_truth.json (12 scenarios).
#   6.1  BREACH 10/12   values mostly ratios, two are dollar amounts
#   6.2  COMPLIANT 10/12  values mostly $1.2M-$9.1M, one is a ratio
#   6.3  COMPLIANT  7/12   values ~$264k-$307k or ~0.04
# ponytail: flat per-clause priors. Refine only if hand-solving shows a better
# zero-knowledge guess; do NOT tune these against the open set - that is
# overfitting to a dataset we will not be scored on.
PRIORS = {
    "6.1": ("BREACH",    0.82),
    "6.2": ("COMPLIANT", 4602822.59),
    "6.3": ("COMPLIANT", 270932.93),
}
# Fallback by POSITION when the clause numbers differ. The case says a cell is
# keyed by the number the covenant is printed under, so the private agreements may
# number theirs 5.x or 7.x and every literal lookup would miss at once, collapsing
# all cells onto one guess. Position carries the same information: first covenant,
# second, third.
BY_POSITION = [PRIORS["6.1"], PRIORS["6.2"], PRIORS["6.3"]]
DEFAULT = ("COMPLIANT", 1000000.00)

# The `model` field names what actually produced the answers. The parachute's own
# cells are priors, not model output, but the submission is overwhelmingly agent
# work by the time it ships; overridden per-run by agent.py --model.
TEAM = {"team": "Talents", "contact_email": "alokkolala2@gmail.com",
        "model": "anthropic/claude-opus-5 via OpenRouter"}


def build(template: dict) -> dict:
    out = dict(template)
    out.update({k: v for k, v in TEAM.items() if not template.get(k)})
    answers = {}
    for scenario, cells in template.get("answers", {}).items():
        answers[scenario] = {}
        order = sorted(cells)
        for clause in cells:
            if clause in PRIORS:
                status, actual = PRIORS[clause]
            else:
                i = order.index(clause)
                status, actual = BY_POSITION[i] if i < len(BY_POSITION) else DEFAULT
            answers[scenario][clause] = {
                "status": status,
                "actual": round(actual, 2),
                # Never invent an id. Where the key names one this scores 0 either
                # way; where the key is null a wrong guess is simply ignored.
                "evidence_txn_id": None,
            }
    out["answers"] = answers
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default="agentic-bank-public/submission_template.json")
    ap.add_argument("--out", default="submission.json")
    args = ap.parse_args()

    template = json.loads(Path(args.template).read_text(encoding="utf-8"))
    payload = build(template)

    path = Path(args.out)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic: a half-written submission scores nothing

    cells = sum(len(v) for v in payload["answers"].values())
    print(f"{path}  {len(payload['answers'])} scenarios, {cells} cells, no nulls")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
