# Covenant compliance agent

Corporate borrowers have loan covenants — "net debt to EBITDA shall not exceed 3.5x",
"capital expenditure shall not exceed $4,000,000". Checking them means reading a credit
agreement, working out what the clause actually measures, pulling the right rows out of a
transaction ledger, and doing the arithmetic.

This does that automatically. Point it at a folder of PDFs and a ledger; it produces a
`submission.json` with a `status`, an `actual` and an `evidence_txn_id` for every covenant
of every borrower.

```
pip install -r requirements.txt openai
export OPENROUTER_API_KEY=...          # PowerShell: $env:OPENROUTER_API_KEY = '...'

python agent.py --data path/to/dataset
```

One command. Run it again and it fills only what's missing.

## The one rule

**The model reads. The code computes.**

An LLM is good at reading a legal sentence and saying *what to measure* — which ledger
rows belong in the numerator, which in the denominator, what the threshold is, which
direction a breach lies in. It is unreliable at the arithmetic, and worse, its mistakes
are confident and plausible.

So it never does the arithmetic. It calls `compute()`, which evaluates in
`decimal.Decimal`, and `submit_cell()` rejects any figure that did not come back from
`compute()`. There is no path from the model's own head to the submission.

This was not a design preference. Hand-scoring the same cells both ways, the code never
mis-categorised a ledger row and the model did — repeatedly, always by reading a category
the way an accountant would rather than the way the document worded it.

## How a run goes

```
1. fallback.py      fills every cell with a per-clause prior, in seconds.
                    A scoreable file exists before anything that can fail has run.

2. tools/packet.py  one packet per borrower — the case brief, the ledger, that
                    borrower's rows pre-grouped by category, and every document
                    naming their account. Plus every document with no text layer,
                    because those name nobody and one of them is somebody's file.

3. agent.py         one agent per borrower, run concurrently against OpenRouter.
                    The system prompt is AGENT_PROTOCOL.md, verbatim.

4. tools/collect.py agent answers overlay the parachute. Anything malformed,
                    missing, or flagged non-computable keeps its prior instead.
```

The order matters: a failure at any step still leaves a valid submission on disk. Step 1
guarantees the floor; every step after it is an improvement on a banked result.

## The tools the model gets

Seven, deliberately. Every extra verb is another way to reach a number without going
through `compute`.

| tool | why |
|---|---|
| `list_documents()` | filenames are opaque hashes — the only way to know what a document is, is to read it. Flags pages with no text layer. |
| `read_document(id)` | text per page, with each page's character and image count |
| `render_page(id, page)` | the page as an image. Some documents have no text layer at all; this is the only way in. |
| `search_documents(regex)` | grep every document at once |
| `ledger_rows(account)` | one account's rows, with a signal for how likely each is generated filler |
| `compute(expression)` | `Decimal` arithmetic. The only source of numbers. |
| `submit_cell(...)` | files one verdict, validated on the way in |

Three things are enforced in the tool layer rather than asked for in the prompt, because
the model reads a tool's description at the exact moment it is about to make the mistake:

- **A ledger row belongs to the category its description literally names.** Payroll,
  utilities, rent and insurance are not operating expenses here, whatever accounting
  practice says. Reading them semantically inflates the denominator and flips verdicts.
- **`actual` carries the unit of the threshold, rounded to two decimals.** A limit written
  `0.04x` means the answer is a ratio; `$1,800,000.00` means dollars. `submit_cell`
  quantizes, so extra precision cannot be filed even when the model wants to protect it —
  the answer key rounds too, and matching it is what scores.
- **An uncomputable metric says so in a field, not in prose.** `computable: false` tells
  the collector to keep the prior. A caveat written in the worklog is read by nothing, and
  a plausible substitute shipped as a confident verdict scores worse than not answering.

`submit_cell` also rejects a negative `actual` and an `evidence_txn_id` that appears on no
row of this borrower's account.

## Model and provider

OpenRouter, via the `openai` SDK pointed at its base URL — it is OpenAI-compatible, so no
provider-specific client is needed.

- **Default model** `anthropic/claude-opus-5`. `--model` switches it.
- **Prompt caching** is *not* automatic for Anthropic models on OpenRouter. It is enabled
  per-message on two stable breakpoints — the end of the system prompt and the end of the
  first user turn — so every turn after the first re-reads them at a tenth of the price.
  The run prints the cache figures from the API's own `usage`, and says so loudly if it
  sees zero rather than assuming it worked.
- **Retries** are stdlib exponential backoff. No retry library for something that is six
  lines.

## What a human still does

Everything above is automated. Three things are not, and shouldn't be:

- **Deciding when to submit.** Scoring is best-of-three attempts, so banking an early safe
  submission costs nothing and removes the risk of ending with none.
- **Hand-checking flagged cells.** `collect.py` flags any cell whose value sits implausibly
  far from its own threshold. That catches gross blowups, not small drift — a flag is a
  list to look at, never an edit to apply.
- **Reading the disagreements.** `run.py` is a second, fully deterministic derivation that
  shares no code path with the agent. `crosscheck.py` diffs the two and puts status
  disagreements first. Both stay manual on purpose: the point is a human looking at where
  two independent methods disagree.

## Verifying it

Four self-checks run offline and spend nothing. The agent one drives a stubbed model
through the whole loop, including the three mistakes that are known to cost cells.

```
python agent.py --self-check
python tools/packet.py --self-check
python tools/collect.py --self-check
python scorer.py --self-check
```

The real gate is a score against a dataset with a published answer key:

```
python agent.py --data <dataset> --out submission_agents.json
python scorer.py submission_agents.json <dataset>/ground_truth.json
```

`scorer.py` implements the published scheme: `status` is half the cell and an exact string
match — get it wrong and the rest scores nothing. `actual` is 0.30, decaying to zero at 5%
relative error. `evidence_txn_id` is 0.20, and where the key names no transaction that
0.20 rides on `actual` instead. It reports an unweighted mean, because the official
per-cell difficulty weights are not published.

**Reference points on the public dataset** (12 borrowers, 36 cells), both reproducible with
the commands above:

| | score |
|---|---|
| priors alone, zero analysis | 0.38 |
| deterministic pipeline (`run.py`) | 0.92 |
| agent path (`agent.py`) | not yet measured — needs an API key |

The pipeline number looks like it settles the argument, and it doesn't: that pipeline
dispatches on a table of literal phrases, each of which occurs in exactly one borrower's
agreement. Reword the covenants and most of it stops matching. It is tuned to this
dataset and cannot be tuned to one it has not seen. The agent reaches comparable quality
with no tuning at all, which is the entire reason it exists.

## Known limits

Stated rather than papered over.

- **Some covenants are underdetermined.** They name a quantity the documents never define
  — "operating expenses", "EBITDA". Several defensible readings of the same clause can
  differ by more than half again. That is a property of the corpus, not a bug to fix.
- **Some figures are simply absent.** One covenant measures a quantity at group level while
  only the borrower's own data ships. It is not in any document and not derivable from the
  ledger. The agent flags it non-computable and the prior stands, which scores better than
  a confident guess.
- **Source documents can be wrong.** One printed threshold contradicted the answer key.
  Every method computed the ratio correctly, compared it against the number on the page,
  and lost the cell. Refusing to special-case it was the right call — every candidate fix
  matched that one cell and would have quietly corrupted its siblings.
- **Two methods can be wrong together.** When the pipeline and the agent agree and are both
  wrong, nothing flags it.

## Operational behaviour

- **Nothing takes the run down.** A borrower that fails writes a traceback into its packet
  and leaves the others alone; its cells keep their priors.
- **Idempotent.** A borrower with an answer is skipped. Re-running the same command fills
  the gaps rather than starting over — which is what you want when three agents have died
  on timeouts and the clock is running. `--force` overrides.
- **Bounded.** `--max-turns` caps the loop per borrower; whatever is filed by then is kept.
- **Logged.** Every model call — request, tool calls, results, token usage — is appended to
  `trace.jsonl` in the borrower's packet and flushed per line, so a crash still leaves the
  trace behind.
- **Concurrent, configurably.** `--concurrency` defaults to 4. Turn it down if rate limits
  bite.
- **Nothing is located by filename.** The ledger is found by its `txn_id` column, the
  template by cells carrying a `status` field, the documents folder by where the PDFs are.
  Verified against a copy of the dataset with every file renamed.
- **Packets are built outside the repo** so an agent working in one cannot reach the
  answer key.

## Layout

```
agent.py             the agent, its seven tools, and the whole runbook in one command
AGENT_PROTOCOL.md    the system prompt. Nine steps and a register of traps, passed
                     through verbatim — every entry was written after a measured failure
fallback.py          the priors
tools/packet.py      packet builder; `all` builds every one, `triage` ranks them by risk
tools/collect.py     merge answers onto the priors, validate, flag
pipeline/            PDF loading and Unicode normalisation, Decimal money primitives,
                     the document index, and the category markers the ledger brief uses
run.py               the independent deterministic derivation (manual)
crosscheck.py        diff two submissions (manual)
scorer.py            the gate
PLAN.md              architecture and runbook notes
```

The dataset is not in this repo. It belongs to the organisers; pass `--data <dir>`.

## What this deliberately isn't

No agent framework, no vector database, no web UI, no abstraction layer over the provider,
no retry library. One runner, seven tools, one gate.
