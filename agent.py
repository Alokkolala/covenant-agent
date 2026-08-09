#!/usr/bin/env python
"""The covenant agent, and the whole runbook in one idempotent command.

    python agent.py --data agentic-bank-public                  # parachute -> packets -> agents -> collect
    python agent.py --data <private> --scenario P6 --scenario P3
    python agent.py --self-check                                # exercises the loop, spends nothing

One entry point takes `--data` once and threads it through every step, because
the two things that actually go wrong under time pressure are steps run out of
order and a `--data` path passed to one step and forgotten in the next.

Re-running is safe and is the point: a borrower that already has an `answer.json`
is skipped, so at 12:40 with three agents dead on timeouts the same command fills
the gaps. The parachute runs first, so a scoreable file exists before anything
that can fail has run.

**The model reads. The code computes.** The model's job is to say what to measure
- which rows, which threshold, which direction. `compute()` does the arithmetic in
`Decimal`, and `submit_cell()` refuses any number that did not come out of it. The
split is not stylistic: over eighteen hand-scored cells the code never
mis-categorised a ledger row and an agent doing it semantically got it wrong three
times.

Provider is OpenRouter, reached with the `openai` SDK pointed at its base_url.
Any tool-and-vision model there will do; `--model` switches it. Prompt caching is
asked for differently per provider - OpenAI caches automatically, Anthropic must
be told per message block - so `_cached()` decides from the slug, and the cache
figures are reported from `usage` after every run rather than assumed.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import pymupdf as fitz

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import collect  # noqa: E402
import fallback  # noqa: E402
import packet  # noqa: E402
from pipeline import ingest, ocr  # noqa: E402

BASE_URL = "https://openrouter.ai/api/v1"
MODEL = "openai/gpt-5.6-luna"
CENTS = Decimal("0.01")
STATUSES = ("COMPLIANT", "BREACH")
NO_TEXT = 60  # chars: below this a page carrying images is a raster, not text

_print_lock = threading.Lock()


def say(*a) -> None:
    with _print_lock:
        print(*a, flush=True)


# ---------------------------------------------------------------- the tools

class Toolbox:
    """The seven operations, bound to one borrower's packet.

    Seven agent runs were observed solving this task by hand and they converged
    on exactly these. The model gets these and nothing more: every extra verb is
    a way to reach a number without going through `compute`.

    Everything is located inside the packet by `packet.find()`, by shape rather
    than by name, so tomorrow's archive can call its files whatever it likes.
    """

    def __init__(self, box: Path, scenario: str):
        parts = packet.find(box)
        self.box, self.scenario = box, scenario
        self.docs = {p.stem: p for p in sorted(parts["documents"].glob("*.pdf"))}
        with parts["ledger"].open(encoding="utf-8-sig") as ledger:
            self.ledger = list(csv.DictReader(ledger))
        self.account = packet.account_of(scenario, box)
        self.seen = packet.seen_counts(self.ledger)
        self.txn_ids = {r["txn_id"] for r in self.ledger if r["account_id"] == self.account}
        template = json.loads(parts["template"].read_text(encoding="utf-8"))
        self.clauses = list(template["answers"][scenario])
        self.brief = (box / "ledger_brief.md").read_text(encoding="utf-8")
        self.case = parts["case"].read_text(encoding="utf-8") if parts.get("case") else ""
        self.computed: set[str] = set()   # every figure the CODE has produced, at 2dp
        self.answers: dict[str, dict] = {}
        self.images: list[dict] = []      # rendered pages waiting to be shown
        self._pages: dict[str, list[tuple[str, int]]] = {}

    # -- reading

    def pages(self, doc_id: str) -> list[tuple[str, int]]:
        """(text, image_count) per page, from the TEXT LAYER ONLY.

        Deliberately not routed through `ingest.load_pdf`, which fills rasterised
        pages from the OCR sidecars committed for the open set. Those sidecars
        will not exist tomorrow, so reading through them would measure a comfort
        that is about to disappear. A page with no text layer reads as empty here
        and the agent has to render it - which is the real condition.
        """
        if doc_id not in self._pages:
            with fitz.open(self.docs[doc_id]) as pdf:
                self._pages[doc_id] = [
                    (ingest.normalize_text(p.get_text("text")), len(p.get_images()))
                    for p in pdf
                ]
        return self._pages[doc_id]

    def _resolve(self, doc_id: str) -> str | None:
        doc_id = (doc_id or "").strip()
        for cand in (doc_id, Path(doc_id).stem):
            if cand in self.docs:
                return cand
        return None

    def list_documents(self) -> str:
        out = [f"{len(self.docs)} documents in this packet. Filenames are opaque hashes; "
               f"open each one and decide what it is by reading it."]
        for doc_id, path in self.docs.items():
            pg = self.pages(doc_id)
            blind = [i + 1 for i, (t, n) in enumerate(pg) if len(t.strip()) < NO_TEXT]
            note = (f"  <- NO TEXT LAYER on page(s) {blind}: they match no text search "
                    f"and read as empty. render_page them." if blind else "")
            out.append(f"  {doc_id}  {len(pg)} page(s), {sum(len(t) for t, _ in pg)} chars{note}")
        return "\n".join(out)

    def read_document(self, doc_id: str) -> str:
        if not (d := self._resolve(doc_id)):
            return f"no document {doc_id!r} here. Available: {', '.join(self.docs)}"
        pg = self.pages(d)
        out = []
        for i, (text, n_img) in enumerate(pg, 1):
            head = f"--- {d} page {i}/{len(pg)}  chars={len(text.strip())} images={n_img}"
            if len(text.strip()) < NO_TEXT and n_img:
                head += "  NO TEXT LAYER - render_page to read this page"
            out.append(f"{head} ---\n{text}")
        return "\n".join(out)

    def render_page(self, doc_id: str, page: int, dpi: int = 200) -> str:
        if not (d := self._resolve(doc_id)):
            return f"no document {doc_id!r} here. Available: {', '.join(self.docs)}"
        n = len(self.pages(d))
        if not isinstance(page, int) or not 1 <= page <= n:
            return f"{d} has pages 1..{n}; asked for {page!r}"
        png = ocr.render_pages(self.docs[d], dpi=int(dpi))[page - 1]
        self.images.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(png).decode()}})
        return f"{d} page {page} rendered at {dpi} dpi. The image follows this message."

    def search_documents(self, pattern: str) -> str:
        try:
            rx = re.compile(pattern, re.I | re.M)
        except re.error as e:
            return f"not a valid regex: {e}"
        hits = []
        for doc_id in self.docs:
            for i, (text, _) in enumerate(self.pages(doc_id), 1):
                for m in rx.finditer(text):
                    s, e = max(0, m.start() - 140), min(len(text), m.end() + 140)
                    hits.append(f"{doc_id} p{i}: ...{' '.join(text[s:e].split())}...")
                    if len(hits) >= 80:
                        return ("\n".join(hits) + "\n[80 hits, stopped. Narrow the pattern.]")
        if not hits:
            return (f"no match for {pattern!r} in any text layer. Remember a page with no "
                    f"text layer matches nothing - check list_documents for those.")
        return "\n".join(hits)

    def ledger_rows(self, account: str | None = None) -> str:
        acc = (account or self.account).strip()
        rows = [r for r in self.ledger if r["account_id"] == acc]
        if not rows:
            return (f"no rows on account {acc!r}. A related party genuinely absent from the "
                    f"ledger contributes zero - but confirm the account id first; identity "
                    f"is resolved by account number, never by company name.")
        out = [f"{len(rows)} rows on {acc}" + ("" if acc == self.account else
                                               "  (NOT this borrower's own account)"),
               "seen=N is how many distinct accounts in the whole file share that "
               "description's wording: 1 = planted, high = filler template.", "",
               "txn_id | date | amount | cur | seen | description | counterparty"]
        for r in sorted(rows, key=lambda r: r["txn_id"]):
            amt = r["amount"].strip() or "**BLANK**"
            out.append(f"{r['txn_id']} | {r['date']} | {amt} | {r['currency']} | "
                       f"{self.seen[packet.stem(r['description'])]} | {r['description']} | "
                       f"{r['counterparty']}")
        return "\n".join(out)

    # -- arithmetic: the boundary

    _EXPR = re.compile(r"^[\d\s.+\-*/()]+$")

    def compute(self, expression: str) -> str:
        e = (expression or "").strip()
        if not e or not self._EXPR.match(e):
            return ("compute() evaluates arithmetic only: digits, . + - * / and brackets. "
                    "No names, no functions. Substitute the figures yourself and pass the "
                    "expression.")
        wrapped = re.sub(r"\d+(?:\.\d+)?", lambda m: f'Decimal("{m.group(0)}")', e)
        try:
            value = Decimal(eval(wrapped, {"__builtins__": {}}, {"Decimal": Decimal}))
        except Exception as ex:                                   # noqa: BLE001
            return f"cannot evaluate {e!r}: {type(ex).__name__}: {ex}"
        two = value.quantize(CENTS, rounding=ROUND_HALF_UP)
        self.computed.add(str(two))
        return (f"{e}\n  = {value}\n  = {two} rounded to two decimals, which is what "
                f"submit_cell will file. Compare before rounding, report rounded.")

    # -- filing

    def submit_cell(self, clause: str, status: str, actual, evidence_txn_id=None,
                    threshold=None, computable: bool = True) -> str:
        if clause not in self.clauses:
            return f"unknown clause {clause!r}; this scenario has {self.clauses}"
        if status not in STATUSES:
            return f"status must be exactly one of {STATUSES}, not {status!r}"
        try:
            value = Decimal(str(actual))
        except (InvalidOperation, ValueError):
            return f"actual {actual!r} is not a number"
        if not value.is_finite():
            return f"actual {actual!r} is not finite"
        if value < 0:
            return ("actual is the real value of the metric the covenant constrains and is "
                    "positive, even where the covenant is breached. Check the sign of the "
                    "expression rather than negating the answer.")
        two = value.quantize(CENTS, rounding=ROUND_HALF_UP)
        if str(two) not in self.computed:
            return (f"{two} did not come out of compute(). No number you produce reaches the "
                    f"submission - call compute() with the arithmetic and file what it "
                    f"returns. If the figure needs no arithmetic, pass it to compute() on "
                    f"its own.")
        if evidence_txn_id is not None:
            if not isinstance(evidence_txn_id, str) or evidence_txn_id not in self.txn_ids:
                return (f"{evidence_txn_id!r} is on no ledger row of account {self.account}. "
                        f"Never invent an id to look complete; pass null unless one "
                        f"transaction carries a judgment and removing it flips the verdict.")
        cell = {"status": status, "actual": float(two), "evidence_txn_id": evidence_txn_id}
        if threshold is not None:
            try:
                cell["_threshold"] = float(Decimal(str(threshold)))
            except (InvalidOperation, ValueError):
                pass
        if computable is False:
            cell["_computable"] = False
        self.answers[clause] = cell

        left = [c for c in self.clauses if c not in self.answers]
        tail = (f"Still to file: {left}." if left else
                "Every cell is filed; the run ends now.")
        note = ("  This cell is flagged not computable, so the collector will keep its prior "
                "instead of your proxy - which is the point of the flag."
                if computable is False else "")
        return f"filed {clause}: {status} actual={two} evidence={evidence_txn_id}.{note} {tail}"

    # -- dispatch

    def dispatch(self, name: str, raw_args: str) -> str:
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError as e:
            return f"arguments were not valid JSON: {e}"
        fn = getattr(self, name, None)
        if not callable(fn) or name.startswith("_") or name not in {t["function"]["name"]
                                                                    for t in TOOLS}:
            return f"no tool called {name!r}"
        try:
            return str(fn(**args))
        except TypeError as e:
            return f"{name}: wrong arguments: {e}"
        except Exception as e:                                    # noqa: BLE001
            return f"{name} failed: {type(e).__name__}: {e}"


# The descriptions carry the three failure modes that each cost a measured cell.
# They belong here as much as in the protocol: the model reads a tool description
# at the moment it uses the tool, which is the moment the mistake is made.
TOOLS = [
    {"type": "function", "function": {
        "name": "list_documents",
        "description": "Every document in this borrower's packet, with page count and which "
                       "pages carry no text layer. Filenames are opaque hashes, so the only "
                       "way to know what a document is, is to read it. If your haul looks "
                       "smaller than a sibling borrower's, the missing one is probably a "
                       "scan, not an absence.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "read_document",
        "description": "The text layer of a document, page by page, with each page's "
                       "character count and image count. A page reading chars=0 with "
                       "images>0 is rasterised: its content is real and invisible here, and "
                       "a five-page document can read fine while its load-bearing page is a "
                       "picture. render_page those.",
        "parameters": {"type": "object", "required": ["doc_id"], "properties": {
            "doc_id": {"type": "string", "description": "id from list_documents"}}}}},
    {"type": "function", "function": {
        "name": "render_page",
        "description": "Rasterise one page and show it to you as an image. The only way to "
                       "read a page with no text layer - on a measured dataset one such file "
                       "held a borrower's entire KYC dossier, and the covenant resting on it "
                       "was unsolvable without rendering it.",
        "parameters": {"type": "object", "required": ["doc_id", "page"], "properties": {
            "doc_id": {"type": "string"},
            "page": {"type": "integer", "description": "1-indexed"},
            "dpi": {"type": "integer", "description": "200 is enough for 8pt body text"}}}}},
    {"type": "function", "function": {
        "name": "search_documents",
        "description": "Python regex across every document's text layer at once, "
                       "case-insensitive, with surrounding context. Note what it cannot do: "
                       "a document with no text layer matches nothing, so a search by "
                       "account id silently under-reports how many documents this borrower "
                       "has.",
        "parameters": {"type": "object", "required": ["pattern"], "properties": {
            "pattern": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "ledger_rows",
        "description": "Ledger rows for an account, with seen=N (how many distinct accounts "
                       "share that description's wording: 1 means planted, high means filler "
                       "template). Defaults to this borrower. Pass another account id to "
                       "resolve a related party - identity is by account number, never by "
                       "company name.\n\nA row belongs to the category its description "
                       "LITERALLY NAMES, not the category accounting practice would put it "
                       "in. Payroll, utilities, rent and insurance are not operating "
                       "expenses here. Reading them semantically inflated a denominator by "
                       "1.55x and turned a BREACH into a COMPLIANT - the single most "
                       "expensive mistake measured on this task.",
        "parameters": {"type": "object", "properties": {
            "account": {"type": "string", "description": "defaults to this borrower's"}}}}},
    {"type": "function", "function": {
        "name": "compute",
        "description": "Evaluate arithmetic in decimal.Decimal and return the exact result "
                       "and its two-decimal rounding. Digits, . + - * / and brackets only. "
                       "This is the only source of numbers for the submission: substitute "
                       "the figures you have read into an expression and file what comes "
                       "back. submit_cell rejects anything else.",
        "parameters": {"type": "object", "required": ["expression"], "properties": {
            "expression": {"type": "string", "description": "e.g. (1200000 + 340000) / 2100000"}}}}},
    {"type": "function", "function": {
        "name": "submit_cell",
        "description": "File one covenant's verdict. Validated on the way in and rounded to "
                       "exactly two decimals, because the answer key rounds the same way - "
                       "an agent that filed 0.04339 to protect precision scored zero on that "
                       "field where 0.04 scored full marks.",
        "parameters": {"type": "object",
                       "required": ["clause", "status", "actual", "threshold"],
                       "properties": {
            "clause": {"type": "string", "description":
                       "the exact covenant key listed in this borrower's task"},
            "status": {"type": "string", "enum": list(STATUSES)},
            "actual": {"type": "number", "description":
                       "The real value of the metric the covenant constrains, positive, "
                       "IN THE SAME UNIT AS THE THRESHOLD. A limit written as a multiple "
                       "(0.04x, 1.70x) means this is the ratio; a limit written as money "
                       "($1,800,000.00) means this is the money amount. Read the limit's "
                       "notation - not the clause heading, not the grammar of the sentence. "
                       "Must be a figure compute() returned."},
            "evidence_txn_id": {"type": ["string", "null"], "description":
                                "A transaction id only where removing it flips the verdict "
                                "AND a judgment attaches to it - an auditor reclassification, "
                                "a KYC determination, a correction. A row that merely "
                                "contributes to a sum is not evidence. null otherwise; never "
                                "invent one."},
            "threshold": {"type": "number", "description":
                          "The covenant's own limit as a number, in the same unit. A working "
                          "note, not submitted: it lets the collector check actual against "
                          "the limit."},
            "computable": {"type": "boolean", "description":
                           "false when the metric cannot be computed from what the documents "
                           "and ledger actually provide - a figure measured at group level "
                           "when only the borrower's own data ships, a term defined nowhere. "
                           "Still file your best proxy, but this flag makes the collector "
                           "keep the prior instead. Measured: a self-declared proxy shipped "
                           "as a confident verdict scored 0 where the untouched prior scored "
                           "0.5. A prose caveat is not read by anything."}}}}},
]

TASK = """You are solving the covenants of ONE borrower: scenario {scenario}, account {account}.

Fill exactly these cells: {clauses}. File each with `submit_cell` when you are finished
with it — the run ends the moment the last one is filed, so do not file a draft and come
back to it.

These keys are addresses, not meanings. Find every exact key in the governing agreement
wherever it appears. Never assume a particular article, numbering scheme, or number of covenants.

Everything you need is reachable through the tools; there is no filesystem. Work the nine
steps of the protocol in order. Below are the case brief, and a ledger brief in which the
code has pre-grouped this borrower's rows by the category markers — that grouping is a
proposal to check against the descriptions, not an answer.

═══════════════════ CASE ═══════════════════
{case}

═══════════════════ LEDGER BRIEF — {account} ═══════════════════
{brief}
"""


# ------------------------------------------------------------------ the loop

def _cached(part: dict, model: str) -> dict:
    """Mark a content block as a cache breakpoint, where the provider wants one.

    The ~20KB protocol and the ~17KB brief are resent on every turn of every tool
    loop, so caching them is the difference between paying for them once and
    paying ~40 times. How you ask for it depends on who is answering:

    - **Anthropic** on OpenRouter does NOT cache automatically. It must be asked,
      per content block. Two breakpoints of the four allowed: end of the system
      prompt, end of the first user message. Both are stable for the whole run.
    - **OpenAI** caches automatically above ~1K tokens and takes no such field.
      Sending one is at best ignored, so it is not sent.

    Either way the run reports what actually happened from `usage`, because a
    cache you believe in and do not have is the expensive kind.
    """
    if not model.startswith("anthropic/"):
        return part
    return {**part, "cache_control": {"type": "ephemeral"}}


def call_model(client, messages, cfg, log) -> object:
    """One completion, retried on anything transient. stdlib only, no tenacity."""
    delay = 4.0
    for attempt in range(1, cfg.retries + 1):
        try:
            resp = client.chat.completions.create(
                model=cfg.model, messages=messages, tools=TOOLS,
                max_tokens=cfg.max_tokens, timeout=cfg.timeout,
            )
            if not getattr(resp, "choices", None):
                raise RuntimeError(f"no choices in response: "
                                   f"{getattr(resp, 'error', None) or resp}")
            return resp
        except Exception as e:                                    # noqa: BLE001
            log({"event": "retry", "attempt": attempt, "error": f"{type(e).__name__}: {e}"})
            if attempt == cfg.retries:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60.0)


def as_dict(msg) -> dict:
    """The assistant turn, back into the wire shape, so it can be replayed."""
    out = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [{"id": tc.id, "type": "function",
                              "function": {"name": tc.function.name,
                                           "arguments": tc.function.arguments}}
                             for tc in msg.tool_calls]
    return out


def solve(box: Path, scenario: str, cfg, client) -> dict:
    """Run one borrower to an answer.json. Returns a usage summary."""
    tb = Toolbox(box, scenario)
    trace = (box / "trace.jsonl").open("a", encoding="utf-8")

    def log(rec: dict) -> None:
        rec["t"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        trace.write(json.dumps(rec, ensure_ascii=False) + "\n")
        trace.flush()          # a crash at 13:30 must still leave the trace behind

    protocol = (box / "AGENT_PROTOCOL.md").read_text(encoding="utf-8")
    task = TASK.format(scenario=scenario, account=tb.account, clauses=tb.clauses,
                       case=tb.case, brief=tb.brief)
    messages = [
        {"role": "system",
         "content": [_cached({"type": "text", "text": protocol}, cfg.model)]},
        {"role": "user",
         "content": [_cached({"type": "text", "text": task}, cfg.model)]},
    ]
    log({"event": "start", "scenario": scenario, "account": tb.account,
         "model": cfg.model, "system": protocol[:400] + "...", "task": task[:1200] + "..."})

    used = {"scenario": scenario, "in": 0, "out": 0, "cached": 0, "turns": 0, "calls": 0}
    try:
        for turn in range(1, cfg.max_turns + 1):
            resp = call_model(client, messages, cfg, log)
            msg = resp.choices[0].message
            if u := getattr(resp, "usage", None):
                det = getattr(u, "prompt_tokens_details", None)
                used["in"] += getattr(u, "prompt_tokens", 0) or 0
                used["out"] += getattr(u, "completion_tokens", 0) or 0
                used["cached"] += (getattr(det, "cached_tokens", 0) or 0) if det else 0
            used["turns"] = turn
            calls = [{"name": tc.function.name, "arguments": tc.function.arguments}
                     for tc in (msg.tool_calls or [])]
            log({"event": "turn", "n": turn, "content": msg.content,
                 "tool_calls": calls,
                 "usage": json.loads(resp.usage.model_dump_json()) if resp.usage else None})

            messages.append(as_dict(msg))
            if not msg.tool_calls:
                if len(tb.answers) == len(tb.clauses):
                    break
                messages.append({"role": "user", "content":
                                 f"Still unfiled: {[c for c in tb.clauses if c not in tb.answers]}. "
                                 f"Continue, and file each with submit_cell. Never leave a cell "
                                 f"empty - an empty cell scores the same as a wrong one."})
                continue

            for tc in msg.tool_calls:
                result = tb.dispatch(tc.function.name, tc.function.arguments)
                used["calls"] += 1
                log({"event": "tool", "n": turn, "name": tc.function.name,
                     "arguments": tc.function.arguments, "result": result[:4000]})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            if tb.images:   # a tool cannot return an image; show it as the next user turn
                messages.append({"role": "user", "content":
                                 [{"type": "text", "text": "The rendered page:"}] + tb.images})
                tb.images = []

            if len(tb.answers) == len(tb.clauses):
                break
        else:
            log({"event": "capped", "turns": cfg.max_turns, "filed": sorted(tb.answers)})
    finally:
        # Whatever was proved gets written, even after a crash or a cap. Cells that
        # are missing keep their prior in collect.py; the run is never taken down.
        if tb.answers:
            out = box / "answer.json"
            tmp = out.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(tb.answers, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            tmp.replace(out)
        log({"event": "end", "filed": sorted(tb.answers), "usage": used})
        trace.close()

    used["filed"] = len(tb.answers)
    return used


# --------------------------------------------------------------- the runbook

def make_client(cfg):
    from openai import OpenAI
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit(
            "OPENROUTER_API_KEY is not set. The parachute and the packets are done and on "
            "disk; set the key and run this same command again to fill them in.\n"
            "  PowerShell:  $env:OPENROUTER_API_KEY = '<key>'")
    return OpenAI(base_url=BASE_URL, api_key=key, max_retries=0)


def run(cfg) -> int:
    data = Path(cfg.data)
    out = Path(cfg.out)
    packets = Path(cfg.packets)
    parts = packet.find(data)
    template = parts["template"]
    scenarios = cfg.scenario or list(
        json.loads(template.read_text(encoding="utf-8"))["answers"])

    # 1. Parachute. Before anything that can fail, so a scoreable file exists.
    payload = fallback.build(json.loads(template.read_text(encoding="utf-8")))
    payload["model"] = f"{cfg.model} via OpenRouter"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"[1/4] parachute  -> {out}  "
        f"{sum(len(v) for v in payload['answers'].values())} cells, no nulls")

    # 2. Packets. Idempotent: a borrower with an answer.json is left alone.
    say(f"[2/4] packets    -> {packets}")
    packet.build_all(data, packets, force=cfg.force)

    # 3. Agents.
    todo = [s for s in scenarios
            if cfg.force or not (packets / f"{s.lower()}packet" / "answer.json").exists()]
    skipped = [s for s in scenarios if s not in todo]
    if skipped:
        say(f"[3/4] already answered, skipping: {' '.join(skipped)}")
    if todo:
        try:
            client = make_client(cfg)
        except SystemExit as e:
            # No key, no network, no provider: skip the agents but STILL collect.
            # Bailing here would drop answers already on disk from a previous
            # partial run - which is exactly the case idempotence exists for.
            say(f"[3/4] agents     SKIPPED: {e}")
            todo = []
    if todo:
        say(f"[3/4] agents     {cfg.model}, {cfg.concurrency} at a time: {' '.join(todo)}")
        t0 = time.time()

        def one(scen: str) -> dict:
            box = packets / f"{scen.lower()}packet"
            try:
                u = solve(box, scen, cfg, client)
                say(f"       {scen}: {u['filed']}/{len(payload['answers'][scen])} cells, "
                    f"{u['turns']} turns, "
                    f"{u['in']:,} in ({u['cached']:,} cached) / {u['out']:,} out")
                return u
            except Exception as e:                                # noqa: BLE001
                # One borrower falling over must not take the others down.
                (box / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
                say(f"       {scen}: FAILED {type(e).__name__}: {e}  (cells keep their prior)")
                return {"scenario": scen, "in": 0, "out": 0, "cached": 0, "turns": 0,
                        "calls": 0, "filed": 0}

        with ThreadPoolExecutor(max_workers=cfg.concurrency) as pool:
            spend = list(pool.map(one, todo))
        report_spend(spend, time.time() - t0)

    # 4. Collect: answers overlay the parachute, anything malformed keeps its prior.
    say(f"[4/4] collect    -> {out}")
    collect.collect(packets, template, out, model=f"{cfg.model} via OpenRouter")
    return 0


def report_spend(spend: list[dict], secs: float) -> None:
    tin = sum(s["in"] for s in spend)
    tout = sum(s["out"] for s in spend)
    cached = sum(s["cached"] for s in spend)
    ok = [s for s in spend if s["filed"]]
    say(f"\n       {len(ok)}/{len(spend)} borrowers answered in {secs / 60:.1f} min")
    say(f"       tokens: {tin:,} in ({cached:,} of them cache reads) / {tout:,} out")
    if tin and not cached:
        say("       WARNING: zero cache reads. Prompt caching is not working - the "
            "protocol and brief are being re-read at full price every turn.")


# --------------------------------------------------------------- self-check

def self_check() -> None:
    """Exercise the whole loop against a stubbed model. Spends nothing.

    Drives the three things that have actually cost cells: a figure that never
    went through compute(), a six-decimal `actual`, and an invented evidence id.
    """
    import shutil
    import tempfile
    from types import SimpleNamespace as NS

    protocol = (ROOT / "AGENT_PROTOCOL.md").read_text(encoding="utf-8")
    assert "Article 6" not in protocol
    assert "Covenant locations, numbering, and count can vary" in protocol
    assert "Never assume a particular article, numbering scheme, or number of covenants" in TASK

    with tempfile.TemporaryDirectory() as tmp:
        box = Path(tmp) / "x1packet"
        (box / "documents").mkdir(parents=True)
        doc = fitz.open()
        p1 = doc.new_page()
        for i, line in enumerate([
                "CREDIT AGREEMENT - ARTICLE 6 COVENANTS",
                "6.1 The ratio of operating expenses to revenue shall not exceed 0.04x",
                "measured over the financial year ending 31 December 2025.",
                "The ownership table is reproduced on the following page."]):
            p1.insert_text((72, 72 + 16 * i), line)
        # Page 2 is the dangerous shape: a raster inside an otherwise-text PDF.
        # It reads as empty and matches no search, exactly like the real ones.
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 400, 300))
        pix.clear_with(220)
        doc.new_page().insert_image(fitz.Rect(36, 36, 436, 336), pixmap=pix)
        doc.save(box / "documents" / "deadbeef.pdf")
        doc.close()
        with open(box / "master_ledger.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["txn_id", "account_id", "date", "amount", "currency",
                        "description", "counterparty"])
            w.writerow(["TXN-X1-0001", "ACC-9999", "2025-03-01", "43390.00", "USD",
                        "Plant operating and maintenance expenses", "Alpha LLP"])
            w.writerow(["TXN-X1-0002", "ACC-9999", "2025-06-01", "1000000.00", "USD",
                        "Revenue", "Beta LLP"])
        (box / "submission_template.json").write_text(json.dumps({"answers": {"X1": {
            "6.1": {"status": None, "actual": None, "evidence_txn_id": None}}}}),
            encoding="utf-8")
        (box / "CASE.ru.md").write_text("case", encoding="utf-8")
        shutil.copy2(ROOT / "AGENT_PROTOCOL.md", box / "AGENT_PROTOCOL.md")
        (box / "ledger_brief.md").write_text("brief", encoding="utf-8")

        script = [
            [("list_documents", {})],
            [("read_document", {"doc_id": "deadbeef"})],
            [("render_page", {"doc_id": "deadbeef", "page": 2})],
            [("search_documents", {"pattern": r"0\.04"})],
            [("ledger_rows", {})],
            # every one of these must be refused
            [("submit_cell", {"clause": "6.1", "status": "BREACH", "actual": 0.04339,
                              "threshold": 0.04})],              # never went through compute
            [("compute", {"expression": "43390.00 / 1000000.00"})],
            [("submit_cell", {"clause": "6.1", "status": "MAYBE", "actual": 0.04,
                              "threshold": 0.04})],              # not a status
            [("submit_cell", {"clause": "6.1", "status": "BREACH", "actual": 0.04,
                              "evidence_txn_id": "TXN-X1-9999",
                              "threshold": 0.04})],               # invented evidence id
            [("submit_cell", {"clause": "6.1", "status": "BREACH",
                              "actual": float("nan"), "threshold": 0.04})],
            # and this one must land - it is p8packet's measured mistake verbatim,
            # six decimals where the key holds two. The code rounds it; the agent
            # cannot file 0.04339 however sure it is.
            [("submit_cell", {"clause": "6.1", "status": "BREACH", "actual": 0.04339,
                              "evidence_txn_id": "TXN-X1-0001", "threshold": 0.04})],
        ]
        seen: list[str] = []

        class Stub:
            def __init__(self):
                self.chat = NS(completions=NS(create=self.create))
                self.n = 0

            def create(self, *, model, messages, tools, **kw):
                # The image from render_page must have reached the wire as a user turn.
                if self.n == 3:
                    assert any(isinstance(m.get("content"), list)
                               and any(p.get("type") == "image_url" for p in m["content"])
                               for m in messages), "rendered page never reached the model"
                assert "cache_control" not in messages[0]["content"][0], \
                    "OpenAI caches automatically and takes no cache_control field"
                calls = [NS(id=f"c{self.n}{i}", type="function",
                            function=NS(name=n, arguments=json.dumps(a)))
                         for i, (n, a) in enumerate(script[self.n])]
                self.n += 1
                return NS(choices=[NS(message=NS(content=None, tool_calls=calls))],
                          usage=NS(prompt_tokens=100, completion_tokens=10,
                                   prompt_tokens_details=NS(cached_tokens=90),
                                   model_dump_json=lambda: '{"prompt_tokens":100}'))

        # Ask for the cache the way each provider wants it. Getting this backwards
        # is silent and expensive: the protocol and the brief are resent every turn.
        block = {"type": "text", "text": "x"}
        assert _cached(block, "anthropic/claude-opus-5")["cache_control"]["type"] == "ephemeral"
        assert "cache_control" not in _cached(block, "openai/gpt-5.6-luna")

        cfg = NS(model=MODEL, max_tokens=100, timeout=10, retries=1, max_turns=20)
        real_dispatch = Toolbox.dispatch

        def spy(self, name, raw):
            out = real_dispatch(self, name, raw)
            seen.append(out)
            return out

        Toolbox.dispatch = spy
        try:
            used = solve(box, "X1", cfg, Stub())
        finally:
            Toolbox.dispatch = real_dispatch

        assert "NO TEXT LAYER on page(s) [2]" in seen[0], seen[0]
        assert "render_page to read this page" in seen[1], seen[1]
        assert "rendered at 200 dpi" in seen[2], seen[2]
        assert "deadbeef p1" in seen[3], seen[3]
        assert "TXN-X1-0001" in seen[4] and "seen=N" in seen[4], seen[4]
        assert "did not come out of compute()" in seen[5], "ungrounded number was accepted"
        assert "0.04 rounded to two decimals" in seen[6], seen[6]
        assert "status must be exactly" in seen[7], "bad status was accepted"
        assert "on no ledger row" in seen[8], "invented evidence id was accepted"
        assert "is not finite" in seen[9], "non-finite actual was not rejected cleanly"
        assert "filed 6.1" in seen[10], seen[10]

        cell = json.loads((box / "answer.json").read_text(encoding="utf-8"))["6.1"]
        assert cell == {"status": "BREACH", "actual": 0.04,
                        "evidence_txn_id": "TXN-X1-0001", "_threshold": 0.04}, cell
        assert used["filed"] == 1 and used["cached"] == 90 * used["turns"]
        assert (box / "trace.jsonl").read_text(encoding="utf-8").count('"event": "tool"') == 11

    print("agent self-check ok - loop, tools, 2dp rounding, compute gate, evidence check")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=str(ROOT / "agentic-bank-public"),
                    help="the dataset root; its parts are found by shape, not by name")
    ap.add_argument("--out", default=str(ROOT / "submission_agents.json"))
    ap.add_argument("--packets", default=str(Path.home() / "halyk-packets"))
    ap.add_argument("--scenario", action="append",
                    help="repeatable; default is every scenario in the template")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--concurrency", type=int, default=4,
                    help="OpenRouter's rate limits are unknown; turn this down if they bite")
    ap.add_argument("--max-turns", type=int, default=60,
                    help="hard bound per borrower; whatever is filed by then is kept")
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per model call")
    ap.add_argument("--retries", type=int, default=5)
    ap.add_argument("--force", action="store_true",
                    help="re-run borrowers that already have an answer.json")
    ap.add_argument("--self-check", action="store_true")
    cfg = ap.parse_args()
    if cfg.self_check:
        self_check()
        return 0
    return run(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
