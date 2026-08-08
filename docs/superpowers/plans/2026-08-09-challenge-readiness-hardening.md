# Challenge Readiness Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden final challenge submissions, preserve runtime model metadata, make installation reproducible, and reduce the README to startup instructions without changing covenant logic.

**Architecture:** Keep the existing fallback → packets → agents → collector flow. Enforce finite numbers at both the agent tool and collector boundaries, and make the collector construct the exact three-field challenge schema. Pass the selected model label into the collector while retaining the existing hardcoded team and contact email.

**Tech Stack:** Python 3.10+, standard-library `json`, `decimal`, and `math`; PyMuPDF; OpenAI Python SDK through OpenRouter; existing assert-based self-checks.

---

### Task 1: Prepare a runnable local environment

**Files:**
- Use: `requirements.txt`
- Create locally (ignored): `.venv/`

- [ ] **Step 1: Create the virtual environment**

Run:

```powershell
$env:UV_CACHE_DIR='C:\p2\covenant-agent\.uv-cache'
uv venv --python 'C:\Program Files\Autodesk\3ds Max 2024\Python\python.exe' .venv
```

Expected: `.venv\Scripts\python.exe` is created.

- [ ] **Step 2: Install current and soon-to-be-declared runtime dependencies**

Run:

```powershell
$env:UV_CACHE_DIR='C:\p2\covenant-agent\.uv-cache'
uv pip install --python .venv\Scripts\python.exe -r requirements.txt openai
```

Expected: PyMuPDF, pandas, and the OpenAI SDK install successfully.

- [ ] **Step 3: Verify the existing baseline**

Run:

```powershell
.venv\Scripts\python.exe agent.py --self-check
.venv\Scripts\python.exe tools\collect.py --self-check
.venv\Scripts\python.exe scorer.py --self-check
```

Expected: all three commands end in their `self-check ok` messages.

### Task 2: Harden the collector and preserve model metadata

**Files:**
- Modify: `tools/collect.py:21-118`
- Test: `tools/collect.py:121-170`

- [ ] **Step 1: Add failing collector checks**

In `demo()`, give the accepted `6.1` cell an unexpected field, add an `X4/6.1` packet containing `actual: NaN`, call collection with a runtime model, and assert the exact output shape:

```python
"6.1": {"status": "BREACH", "actual": 1.71, "evidence_txn_id": None,
        "_threshold": 1.70, "explanation": "must not leak"},
```

```python
(d / "x4packet").mkdir()
(d / "x4packet" / "answer.json").write_text(json.dumps({
    "6.1": {"status": "BREACH", "actual": float("nan"),
            "evidence_txn_id": None},
}), encoding="utf-8")
tpl["answers"]["X4"] = {"6.1": {"status": None, "actual": None,
                                "evidence_txn_id": None}}
```

```python
collect(d, t, out, model="runtime-model via OpenRouter")
payload = json.loads(out.read_text(encoding="utf-8"))
answers = payload["answers"]
assert payload["model"] == "runtime-model via OpenRouter"
assert set(answers["X1"]["6.1"]) == {"status", "actual", "evidence_txn_id"}
assert answers["X4"]["6.1"]["actual"] == fallback.PRIORS["6.1"][1]
```

- [ ] **Step 2: Run the collector self-check to verify RED**

Run:

```powershell
.venv\Scripts\python.exe tools\collect.py --self-check
```

Expected: FAIL because `collect` does not accept `model`, unexpected keys leak, and `NaN` is accepted.

- [ ] **Step 3: Implement the minimum collector fix**

Import `math`, reject non-finite values, accept an optional model label, and allowlist the graded fields:

```python
import math
```

```python
if isinstance(a, bool) or not isinstance(a, (int, float)) or not math.isfinite(a):
    return f"actual {a!r} is not a finite number"
```

```python
def collect(packets: Path, template: Path, out: Path, model: str | None = None) -> int:
    payload = fallback.build(json.loads(template.read_text(encoding="utf-8")))
    if model:
        payload["model"] = model
```

```python
payload["answers"][scen][clause] = {
    "status": cell["status"],
    "actual": cell["actual"],
    "evidence_txn_id": cell.get("evidence_txn_id"),
}
```

Add `--model` to the standalone collector CLI and pass `a.model` to `collect`.

- [ ] **Step 4: Run the collector self-check to verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe tools\collect.py --self-check
```

Expected: `collect self-check ok`, with `X4/6.1` reported as invalid and kept on its prior.

- [ ] **Step 5: Commit collector hardening**

```powershell
git add tools/collect.py
git commit -m "fix: harden submission collection"
```

### Task 3: Harden the agent boundary and keep the selected model

**Files:**
- Modify: `agent.py:221-254`
- Modify: `agent.py:553-642`
- Test: `agent.py:647-771`

- [ ] **Step 1: Add a failing non-finite tool check**

Add this scripted tool call immediately before the valid final submission in `self_check()`:

```python
[("submit_cell", {"clause": "6.1", "status": "BREACH", "actual": float("nan"),
                  "threshold": 0.04})],
```

Assert it receives a specific validation response and adjust later indices and the tool-event total by one:

```python
assert "is not finite" in seen[9], "non-finite actual was not rejected cleanly"
assert "filed 6.1" in seen[10], seen[10]
assert (box / "trace.jsonl").read_text(encoding="utf-8").count('"event": "tool"') == 11
```

- [ ] **Step 2: Run the agent self-check to verify RED**

Run:

```powershell
.venv\Scripts\python.exe agent.py --self-check
```

Expected: FAIL because the current `Decimal('NaN')` comparison is caught as a generic tool failure rather than the required finite-number validation.

- [ ] **Step 3: Implement finite validation and metadata propagation**

In `Toolbox.submit_cell`, validate before comparing the sign:

```python
if not value.is_finite():
    return f"actual {actual!r} is not finite"
if value < 0:
```

Pass the runtime model into final collection:

```python
collect.collect(packets, template, out, model=f"{cfg.model} via OpenRouter")
```

Delete `RATES` and the approximate dollar calculation from `report_spend`; retain borrower counts and measured input, cached, and output token counts.

- [ ] **Step 4: Run the agent self-check to verify GREEN**

Run:

```powershell
.venv\Scripts\python.exe agent.py --self-check
```

Expected: `agent self-check ok - loop, tools, 2dp rounding, compute gate, evidence check`.

- [ ] **Step 5: Commit agent hardening**

```powershell
git add agent.py
git commit -m "fix: validate agent submissions"
```

### Task 4: Make installation complete and README start-only

**Files:**
- Modify: `requirements.txt`
- Modify: `README.md`

- [ ] **Step 1: Declare the OpenAI runtime dependency**

Set `requirements.txt` to:

```text
# Deliberately tiny. No agent framework or vector database.
pymupdf>=1.24
pandas>=2.0
openai>=1.0
```

- [ ] **Step 2: Replace README with the start-only runbook**

Set `README.md` to:

````markdown
# Covenant compliance agent

## Start

Requires Python 3.10+ and an OpenRouter API key.

```powershell
python -m pip install -r requirements.txt
$env:OPENROUTER_API_KEY = '<your-key>'
python agent.py --data 'C:\path\to\dataset'
```

The completed file is written to `submission_agents.json`.

Useful options:

```text
--out PATH          output JSON path
--model MODEL       OpenRouter model slug
--scenario ID       solve one borrower; repeatable
--concurrency N     parallel borrowers (default: 4)
--force             rebuild and re-solve existing packets
--self-check        verify the agent loop without API calls
```
````

- [ ] **Step 3: Verify installation metadata and README commands**

Run:

```powershell
$env:UV_CACHE_DIR='C:\p2\covenant-agent\.uv-cache'
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
.venv\Scripts\python.exe agent.py --help
```

Expected: dependency installation succeeds and help lists the documented options.

- [ ] **Step 4: Commit startup documentation**

```powershell
git add requirements.txt README.md
git commit -m "docs: reduce readme to startup"
```

### Task 5: Full regression verification

**Files:**
- Verify: all changed Python and JSON paths

- [ ] **Step 1: Run every self-check**

Run:

```powershell
.venv\Scripts\python.exe agent.py --self-check
.venv\Scripts\python.exe tools\packet.py --self-check
.venv\Scripts\python.exe tools\collect.py --self-check
.venv\Scripts\python.exe scorer.py --self-check
.venv\Scripts\python.exe crosscheck.py --self-check
.venv\Scripts\python.exe pipeline\compute.py
.venv\Scripts\python.exe pipeline\solve.py
```

Expected: every command exits zero and prints its success message.

- [ ] **Step 2: Compile all Python sources**

Run:

```powershell
.venv\Scripts\python.exe -m compileall -q agent.py fallback.py run.py scorer.py crosscheck.py pipeline tools
```

Expected: exit code 0 with no output.

- [ ] **Step 3: Re-run the deterministic public pipeline and scorer**

Run:

```powershell
.venv\Scripts\python.exe run.py --data agentic-bank-public --out submission_pipeline.json
.venv\Scripts\python.exe scorer.py submission_pipeline.json agentic-bank-public\ground_truth.json
```

Expected: `SCORE 0.9167` with 34/36 statuses correct and 32/36 full-mark cells.

- [ ] **Step 4: Strictly validate generated JSON**

Run:

```powershell
.venv\Scripts\python.exe -c "import json; from pathlib import Path; p=Path('submission_pipeline.json'); x=json.loads(p.read_text(encoding='utf-8'), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v))); assert set(x)=={'team','contact_email','model','answers'}; assert all(set(c)=={'status','actual','evidence_txn_id'} for s in x['answers'].values() for c in s.values())"
```

Expected: exit code 0 with no output.

- [ ] **Step 5: Review the diff and commit any verification-only adjustments**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors and only intentional files changed.
