# Covenant compliance agent

Loan covenants are promises in a credit agreement — "capital expenditure shall not exceed
$4,000,000", "net debt to EBITDA shall not exceed 3.5x". Checking one means reading the
agreement, working out what the clause actually measures, pulling the right rows out of a
transaction ledger, and doing the arithmetic.

Point this at a folder of PDFs and a ledger. It writes a `submission.json` with a
`status`, an `actual` and an `evidence_txn_id` for every covenant of every borrower.

## Start

```
pip install -r requirements.txt openai
export OPENROUTER_API_KEY=...          # PowerShell: $env:OPENROUTER_API_KEY = '...'

python agent.py --data path/to/dataset
```

That's the whole runbook: priors → packets → agents → collected submission. Run it again
and it fills only what's missing.

```
--model         default anthropic/claude-opus-5
--scenario      repeatable; default is every borrower
--concurrency   default 4
--max-turns     cap per borrower; whatever is filed by then is kept
--force         redo borrowers that already have an answer
--self-check    exercise the whole loop against a stub, spend nothing
```

## The one rule

**The model reads. The code computes.**

The model says *what to measure* — which rows, which threshold, which direction a breach
lies in. It never does the arithmetic: it calls `compute()`, which evaluates in
`decimal.Decimal`, and `submit_cell()` rejects any figure that didn't come back from it.
There is no path from the model's own head to the submission.

Hand-scoring the same cells both ways, the code never mis-categorised a ledger row and the
model did — always by reading a category the way an accountant would, rather than the way
the document worded it.

## What a run does

1. **`fallback.py`** fills every cell with a prior. A scoreable file exists in seconds,
   before anything that can fail has run.
2. **`tools/packet.py`** builds one packet per borrower — the case brief, their ledger rows
   pre-grouped by category, and every document naming their account. Plus every document
   with no text layer, since those name nobody and one of them is somebody's file.
3. **`agent.py`** runs one agent per borrower, concurrently, against OpenRouter. The system
   prompt is `AGENT_PROTOCOL.md`, verbatim.
4. **`tools/collect.py`** overlays the answers onto the priors. Anything malformed, missing
   or flagged non-computable keeps its prior.

A failure at any step still leaves a valid submission on disk.

## The tools the model gets

| tool | why |
|---|---|
| `list_documents()` | filenames are opaque hashes; flags pages with no text layer |
| `read_document(id)` | text per page, with character and image counts |
| `render_page(id, page)` | the page as an image — the only way into a scan |
| `search_documents(regex)` | grep every document at once |
| `ledger_rows(account)` | one account's rows, with a filler signal per row |
| `compute(expression)` | `Decimal` arithmetic. The only source of numbers. |
| `submit_cell(...)` | files one verdict, validated on the way in |

Three rules live in the tool layer rather than the prompt, because the model reads a tool's
description at the moment it's about to make the mistake:

- **A row belongs to the category its description literally names.** Payroll, utilities and
  rent are not operating expenses here, whatever accounting practice says.
- **`actual` carries the unit of the threshold, at two decimals.** `submit_cell` quantizes,
  so extra precision can't be filed — the answer key rounds too.
- **An uncomputable metric says so in a field, not in prose.** `computable: false` keeps the
  prior. A caveat in the worklog is read by nothing.

## Provider

OpenRouter, via the `openai` SDK pointed at its base URL. Prompt caching is *not* automatic
for Anthropic models there — it's enabled per-message on two stable breakpoints, and the run
prints the real cache figures from `usage` rather than assuming it worked. Retries are
stdlib backoff.

## Verify

```
python agent.py --self-check          # and packet.py / collect.py / scorer.py
python scorer.py submission.json <dataset>/ground_truth.json
```

`status` is half the cell and an exact match — wrong, and the rest scores nothing. `actual`
is 0.30, decaying to zero at 5% error. `evidence_txn_id` is 0.20, riding on `actual` where
the key names no transaction.

On the public dataset (12 borrowers, 36 cells): priors alone **0.38**, deterministic
pipeline (`run.py`) **0.92**, agent path **not yet measured**.

That 0.92 doesn't settle it. The pipeline dispatches on literal phrases, each occurring in
exactly one borrower's agreement — reword the covenants and most of it stops matching. The
agent gets comparable quality with no tuning, which is why it exists.

## Known limits

- **Some covenants are underdetermined** — they name a quantity the documents never define.
  Defensible readings of one clause can differ by half again.
- **Some figures are simply absent.** One covenant measures at group level while only the
  borrower's own data ships. Flagged non-computable; the prior stands.
- **Source documents can be wrong.** One printed threshold contradicted the key. Every
  method computed correctly, compared against the page, and lost the cell. Special-casing it
  would have corrupted its siblings.
- **Two methods can be wrong together.** When `run.py` and the agent agree and are both
  wrong, nothing flags it.

## Details worth knowing

- **Idempotent.** A borrower with an answer is skipped; re-running fills gaps rather than
  starting over.
- **Nothing takes the run down.** A borrower that fails writes a traceback into its packet
  and leaves the others alone.
- **Logged.** Every model call — request, tool calls, results, usage — appends to
  `trace.jsonl` in the packet, flushed per line, so a crash still leaves the trace.
- **Nothing is found by filename.** The ledger is identified by its `txn_id` column, the
  template by cells carrying `status`, the documents folder by where the PDFs are. Verified
  against a copy with every file renamed.
- **Packets build outside the repo**, so an agent working in one can't reach the answer key.
- **`run.py` and `crosscheck.py` stay manual** — a second, independent derivation and a diff
  against it. The point is a human reading where two methods disagree.

## Layout

```
agent.py             the agent, its seven tools, and the runbook in one command
AGENT_PROTOCOL.md    the system prompt — nine steps and a register of traps, verbatim
fallback.py          the priors
tools/packet.py      packet builder; `all` builds every one, `triage` ranks by risk
tools/collect.py     merge onto the priors, validate, flag
pipeline/            PDF loading, Decimal money primitives, document index, category markers
run.py               independent deterministic derivation (manual)
crosscheck.py        diff two submissions (manual)
scorer.py            the gate
```

The dataset isn't in this repo — it belongs to the organisers. Pass `--data <dir>`.

No agent framework, no vector database, no web UI, no retry library.
