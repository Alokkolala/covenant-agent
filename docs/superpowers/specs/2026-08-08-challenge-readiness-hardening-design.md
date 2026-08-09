# Challenge Readiness Hardening Design

## Goal

Make the repository safer to run for the Halyk AI Challenge without changing its covenant-solving logic or its existing hardcoded submission identity.

## Scope

- Keep `team` as `Talents` and `contact_email` as `alokkolala2@gmail.com`.
- Preserve the model selected by `agent.py --model` in the final collected submission. The collector must not replace it with the fallback model label.
- Emit exactly `status`, `actual`, and `evidence_txn_id` in every submitted covenant cell. Agent working-note fields and any unexpected fields must not reach the final JSON.
- Reject non-finite numeric values such as `NaN` and infinity at the submission boundary so `json.dumps` cannot produce non-standard JSON.
- Install every runtime dependency through `requirements.txt`, including the OpenAI SDK used for OpenRouter.
- Remove the inaccurate model-price estimator. Continue reporting measured token and cache usage.
- Replace the README with only the prerequisites, installation command, API-key setup, start command, and a compact list of useful run flags.

## Architecture and Data Flow

The existing flow remains unchanged: fallback submission, borrower packets, model agents, then collection. `fallback.build` continues to own the hardcoded team and email. The runtime model label will be passed into collection explicitly, so the same label written before agent execution survives the final rebuild.

The collector remains the only final JSON boundary. It will validate finite non-negative numeric values and construct each accepted cell from an explicit three-field allowlist. `Toolbox.submit_cell` will apply the same finite-number rule earlier, giving the model immediate feedback instead of waiting for collection.

No document parsing, covenant classification, ledger categorisation, arithmetic, evidence selection, fallback priors, or deterministic solver behavior will change.

## Error Handling

- A non-finite agent `actual` is refused and cannot be filed.
- A malformed manually supplied packet cell is rejected by the collector and keeps its fallback prior.
- Unexpected cell keys are ignored at collection rather than copied into the challenge submission.
- Missing API credentials retain the existing behavior: packets and prior-backed output remain available.

## Verification

Focused self-checks will cover runtime model preservation, exact cell keys, non-finite rejection, and the existing fallback behavior. The complete existing self-check set will be run after the changes.

The deterministic public submission will be rescored against `agentic-bank-public/ground_truth.json`; its current unweighted score of `0.9167` must not regress. Python compilation and strict JSON parsing will also be checked.

## Documentation

The README will become a start-only runbook. Detailed architecture and historical measurements already live in source comments and `PLAN.md`, so they will not be duplicated in the README.
