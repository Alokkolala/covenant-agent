# Covenant compliance agent

Reads a bank's messy corporate-lending PDFs and decides, per borrower per covenant,
whether it is breached — emitting `status`, `actual` and `evidence_txn_id` into a
`submission.json` shaped exactly like the provided template.

```
python agent.py --data agentic-bank-public
```

One command. Parachute → packets → agents → collect. Re-run it and it fills only the
gaps.

## The one architectural rule

**The model reads. The code computes. No number the model produces reaches the
submission.**

The model's job is to read a legal clause and say *what to measure*: which ledger rows
go in the numerator, which in the denominator, what the threshold is, which direction a
breach lies in. The arithmetic happens in Python with `decimal.Decimal`, and
`submit_cell()` refuses any `actual` that did not come out of `compute()`.

This is not stylistic. Over eighteen hand-scored cells the code never mis-categorised a
ledger row; an agent doing the same job semantically got it wrong three times.

## How it runs

```
fallback.py        every cell filled with a prior, in seconds — a scoreable file
      │            exists before anything that can fail has run
      ▼
tools/packet.py    one packet per borrower: the case brief, the ledger, a ledger_brief
      │            with rows pre-grouped by category marker, and every document naming
      │            that borrower's account — plus every document with no text layer,
      │            since those name nobody and one of them is somebody's KYC dossier
      ▼
agent.py           one agent per borrower, concurrent, over the OpenRouter API.
      │            System prompt is AGENT_PROTOCOL.md, verbatim.
      ▼
tools/collect.py   answers overlay the parachute; anything malformed, absent or
                   self-declared non-computable keeps its prior
```

Order is chosen so that a failure at any step still leaves a submittable file on disk.

## Model and provider

OpenRouter, reached with the `openai` SDK pointed at `https://openrouter.ai/api/v1`
(it is OpenAI-compatible). No Anthropic SDK, no `thinking`, no native-shape parameters
that OpenRouter does not pass through.

| | |
|---|---|
| default model | `anthropic/claude-opus-5` (`--model` to switch, e.g. `anthropic/claude-sonnet-5`) |
| auth | `OPENROUTER_API_KEY` in the environment |
| caching | **not automatic on OpenRouter** — enabled per message with `cache_control` on two breakpoints: the end of the system prompt and the end of the first user turn. Both are stable for the whole run, so every turn after the first reads them at 0.1×. The run reports actual cache reads from `usage`; if it sees zero it says so loudly rather than assuming. |
| retries | stdlib exponential backoff, 4s → 60s, 5 attempts. No `tenacity`. |

## The tool surface

Seven operations, deliberately no more. Every extra verb is a way to reach a number
without going through `compute`.

| tool | why it exists |
|---|---|
| `list_documents()` | filenames are opaque hashes; the agent must discover what is there. Flags pages with no text layer. |
| `read_document(id)` | text **per page**, with each page's char count and image count |
| `render_page(id, page, dpi=200)` | returns the page as an image. Load-bearing: some documents have no text layer at all. |
| `search_documents(regex)` | grep across every document at once |
| `ledger_rows(account)` | that borrower's rows with the `seen=N` filler signal |
| `compute(expression)` | `Decimal` arithmetic. The only source of numbers. |
| `submit_cell(...)` | files one verdict, validated on the way in |

Three rules are written into the tool descriptions and enforced in code, not left to the
prompt — because the model reads a tool description at the moment it uses the tool, which
is the moment the mistake gets made:

1. **A row belongs to the category its description literally names.** Payroll, utilities,
   rent and insurance are not operating expenses here, whatever IFRS says. Reading it
   semantically inflated a denominator by 1.55× and flipped a BREACH to COMPLIANT.
2. **`actual` carries the unit of the threshold, at exactly two decimals.** `submit_cell`
   quantizes to two places. An agent that filed `0.04339` to protect precision scored zero
   on that field where `0.04` scored full marks.
3. **A metric that cannot be computed says so in a machine-readable field.**
   `computable: false` → `collect.py` keeps the prior. A prose caveat is read by nothing;
   a proxy shipped as a confident verdict scored 0 where the untouched prior scored 0.5.

Also enforced: `submit_cell` rejects an `evidence_txn_id` that is on no ledger row of this
borrower's account, and rejects a negative `actual`.

## What is automated and what a human does

**Automated** — everything in the diagram above. The parachute, packet building, the
agent loop, retries, concurrency, collection, validation, the plausibility band.

**A human does** — three things, all of them judgement:

- **Decides when to submit.** Three attempts, best-of. The runbook banks an early safe
  submission and spends the rest of the window improving it.
- **Hand-checks flagged cells.** `collect.py` flags a cell whose `actual` sits outside
  0.5×–2.0× of its own threshold. That band catches gross blowups, not drift — it missed
  a 12% error on a hand-checked cell. A flag is a list, never an edit.
- **Reads the disagreements.** `run.py` is an independent deterministic derivation;
  `crosscheck.py` diffs it against the agent submission, status disagreements first.
  Both stay manual on purpose.

## Verification

```
python agent.py --self-check                    # exercises the whole loop, spends nothing
python tools/packet.py --self-check
python tools/collect.py --self-check
python scorer.py --self-check
```

The real gate is a scored result against the open set's answer key:

```
python agent.py --data agentic-bank-public --out submission_agents.json
python scorer.py submission_agents.json agentic-bank-public/ground_truth.json
```

`scorer.py` implements the published scoring: `status` 0.50 (exact match, else the whole
cell is zero), `actual` 0.30 decaying linearly to zero at 5% relative error,
`evidence_txn_id` 0.20 — and where the key's evidence is null, that 0.20 rides on
`actual`'s accuracy. It reports the unweighted mean; the official per-cell difficulty
weights are unpublished.

### Measured

| | open set (12 borrowers, 36 cells) |
|---|---|
| parachute alone (`fallback.py`, zero analysis) | 0.3831 |
| deterministic pipeline (`run.py`) | 0.9167 |
| deterministic pipeline, covenants reworded | 0.7109 † |
| agent path (`agent.py`) | **not yet measured — needs `OPENROUTER_API_KEY`** |

† measured with a rewording harness that is not carried into this repo; the other two
rows reproduce with the commands above.

Prior hand-run measurement, six borrowers scored cell by cell: agents following this
protocol averaged **0.8722** against the pipeline's **0.8333** on the same borrowers. That
is a wash on the open set, which is expected — the pipeline was tuned on exactly this
data. The case for the agent is the reworded row: the pipeline's dispatch is a table of
phrases, each occurring in exactly one borrower's agreement, and only 25% of it survives a
rewrite. The agent reaches tuned-pipeline quality with no tuning at all, so it has nothing
to lose when the wording changes.

## Known limits

Reported, not papered over.

- **Underdetermined cells.** Some covenants name a quantity the corpus never defines —
  "operating expenses", "EBITDA". Three defensible readings of one such cell spanned a
  factor of 1.7. That is a property of the documents, not a bug.
- **One cell needs a figure that exists nowhere.** Its covenant measures a quantity at
  group level while only the borrower's own data ships. Searched down to the decompressed
  page content streams; it is not there. The agent flags it `computable: false` and the
  prior survives, which scores half where a proxy scores zero.
- **One cell was a typo in the source document.** Its printed threshold was wrong and the
  key held the right one. Every method computes the ratio correctly, compares it against
  the number on the page, and loses the cell. Refusing to special-case it was correct —
  each candidate mechanism fitted that one cell and would have corrupted its siblings.
- **Both filters are blind to an error two methods share.** When the pipeline and the
  agent agree and are both wrong, nothing flags it. Bounded and known.
- **The plausibility band catches blowups, not drift.** 12% slips through.

## Operational notes

- **Nothing takes the run down.** One borrower failing writes `error.txt` into its packet
  and leaves the others alone; its cells keep their priors.
- **Idempotent.** A borrower with an `answer.json` is skipped, by both `packet.py` and
  `agent.py`. Re-running the same command at 12:40 fills the gaps rather than starting
  over. `--force` to mean it.
- **Bounded.** `--max-turns` (default 60) per borrower; whatever is filed by then is kept.
- **Logged.** Every model call — request, tool calls, results, usage — is appended to
  `trace.jsonl` in the borrower's packet and flushed per line, so a crash still leaves the
  trace behind.
- **Concurrent, configurably.** `--concurrency` (default 4). OpenRouter's rate limits are
  unknown; turn it down if they bite.
- **Nothing is located by name.** `packet.find()` identifies the ledger by its `txn_id`
  column, the template by cells carrying a `status` field, the documents folder by where
  the PDFs are. Verified against a copy with everything renamed.
- **Packets live outside the repo** (`~/halyk-packets` by default) so an agent given a
  packet cannot reach `ground_truth.json`.

## Layout

```
agent.py             the agent, the seven tools, and the whole runbook in one command
AGENT_PROTOCOL.md    the system prompt — 9 steps and a register of 24 traps, passed
                     through verbatim. Every rule was written after a measured failure.
fallback.py          the parachute
tools/packet.py      packet builder; `all` builds every one, `triage` ranks by risk
tools/collect.py     merge answers onto the parachute, validate, flag
pipeline/            PDF loading with Unicode normalisation, Decimal money primitives,
                     the document index, and solve.CATEGORIES — the category markers the
                     ledger brief is built from
run.py               the independent deterministic derivation (manual)
crosscheck.py        diff two submissions (manual)
scorer.py            the gate
PLAN.md              architecture, runbook, known limits
```

## Install

```
pip install -r requirements.txt openai
export OPENROUTER_API_KEY=...        # PowerShell: $env:OPENROUTER_API_KEY = '...'
```

The dataset is not committed — it is the organisers' to distribute. Drop it anywhere and
pass `--data <dir>`.

## What was not built

No agent framework, no vector DB, no web UI, no provider abstraction layer, no retry
library. One runner, seven tools, one gate.
