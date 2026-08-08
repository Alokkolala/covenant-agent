# Plan

**The model reads. The code computes. The parachute is always underneath.**

## Architecture

```
fallback.py        every cell filled with a prior, in seconds
      │
      ▼
mechanics (code)   ledger -> scenario/account map, invariants
                   PDF -> normalised text, per-page raster check
                   packet per borrower: every document naming its account,
                   plus every document with no text layer at all
      │
      ▼
runner (agent)     one agent per borrower, driven over the OpenRouter API.
                   Reads the clause, decides the metric, names the rows.
                   System prompt is AGENT_PROTOCOL.md, verbatim.
      │
      ▼
collect.py         overlay onto the parachute, validate, flag
      │
      ▼
three filters      plausibility band · crosscheck vs pipeline · what the agent doubted
      │
      ▼
hand-check         only the flagged cells
```

Code does what traps cannot break: arithmetic, filtering, invariants, valid JSON. The model
does what code cannot generalise: reading a legal sentence and noticing what is wrong with
a document. **No number the model produces reaches the submission** — the model names which
categories to combine; `Decimal` does the arithmetic.

## Why an agent rather than the solvers

The deterministic pipeline scores 0.9167 on the open set and **0.7109 once the covenant
wording changes** — its dispatch is a table of phrases, each occurring in exactly one
borrower's agreement, and only 25% survive rewording. The private set words covenants
differently by construction.

Across six borrowers scored cell by cell, agents following the protocol averaged **0.8722**
against the pipeline's **0.8333** — a wash on the open set, which is expected: the pipeline
was tuned on exactly this data. The case for the agent is that it reaches tuned-pipeline
quality with no tuning at all, so it has nothing to lose when the wording changes.

**What did not hold up:** the claim that narrowing the document set is what produces the
gain. Handed all 203 documents instead of its own five, an agent scored the same 1.0000 on
the same borrower. Packets are an optimisation — less context, less time — not the source of
quality. The protocol is. Adding two rules to it moved one borrower from 0.6667 to 1.0000
with every other input identical.

**Kept from the pipeline, load-bearing:** `solve.CATEGORIES` and `category_rows` /
`category_total` build the `ledger_brief.md` in every packet. These markers are the
generator's vocabulary, not a borrower's — `operating costs` appears across nine borrowers
under nine prefixes — and they reproduce the key on three independent borrowers across three
covenant shapes. Over eighteen hand-scored cells the code never mis-categorised a row and an
agent did so three times. Deleting `solve.py` breaks packet building.

**Kept as a second opinion:** the 17 per-borrower solvers and the phrase table. Both are
memorisation, but they cost nothing and give `crosscheck.py` an independent derivation to
disagree with.

## Runbook — 3 hours, 3 attempts, best one counts

Best-of-three changes the shape: an early safe submission costs nothing and removes the
zero. Do not spend attempts on intermediate looks.

| Step | Command | Guarantee |
|---|---|---|
| 1 | `python fallback.py --template <private>/...` | a valid, complete file exists within seconds |
| 2 | `python tools/packet.py all --data <private>` | every packet from one index; ~45s for twelve |
| 3 | `python tools/packet.py triage --data <private>` | send agents hardest-first if the window is tight |
| 4 | runner, borrowers concurrently | one agent each, protocol in the packet, idempotent on re-run |
| 5 | `python tools/collect.py --packets ... --data <private>` | agent answers overlay the parachute; anything malformed keeps its prior |
| — | **submit #1** — whatever exists by ~11:30 | banks a floor; the parachute alone beats a missed deadline |
| 6 | `python run.py --data <private> --out submission_pipeline.json` | the second, independent derivation |
| 7 | `python crosscheck.py submission_agents.json submission_pipeline.json` | disagreements, status ones first |
| 8 | hand-check flagged cells only | |
| — | **submit #2** after the hand-checks, **#3** at the deadline | |

Order is chosen so a failure at any step still leaves a submittable file. Steps 1–5 are safe
to re-run: a borrower that already has an `answer.json` is skipped.

`preflight.py` still validates the retired solvers' assumptions, not this flow. Update it or
ignore it; do not trust its FAILs as a gate on the agent path.

## Known limits — report, do not paper over

- **Underdetermined cells.** Some covenants need a quantity the documents never define
  (operating expenses, EBITDA). Three defensible readings of one such cell spanned a factor
  of 1.7. This is a property of the corpus, not a bug to fix.
- **One cell needs a figure that exists nowhere.** Its covenant measures a quantity at group
  level while only the borrower's own data ships; the figure is in no document and no ledger
  aggregate — searched down to the decompressed page content streams. Flag it
  `_computable: false` and keep the prior, which scores half where a proxy scores zero.
- **A second such cell was a typo in the source document** — its printed threshold was wrong
  and the key held the right one, confirmed by the organisers. Every method computed the
  ratio correctly and compared it against the printed number, so every method lost the cell.
  Refusing for two days to special-case it was correct: each candidate mechanism fitted that
  one cell and would have corrupted its siblings. **When a cell resists every reading,
  suspect the input, not only the reasoning.**
- **Both filters are blind to an error two methods share.** When the pipeline and the agent
  agree and are both wrong, nothing flags it. Bounded and known.
