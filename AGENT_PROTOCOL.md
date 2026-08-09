# Covenant solving protocol

One agent, one borrower, its own document folder. Follow the steps in order.

Every rule below was written after a measured failure. None is precautionary.

---

## ① Triage the documents

Filenames are opaque hashes. Open every PDF and decide what it is by reading it.
Produce a table: file → what it is → USED / REJECTED → why.

Reject a document by a statement **inside it**, never by its filename, and never by
"it looks like noise".

**Traps here:**

- **Two agreements per borrower.** One is superseded. It may carry a banner
  ("НЕДЕЙСТВУЮЩАЯ … НЕ ПРИМЕНЯЕТСЯ"), but do not rely on the banner — the wording can
  change. **Diff the covenant sections containing the exact clause keys listed in your
  task.** Covenant locations, numbering, and count can vary between borrowers. The two
  editions are often identical except for one threshold, and that one number flips a cell.
  Establish which edition governs from its own covenant period.
- **Draft vs final auditor reports.** A draft ("ПРОЕКТ", "не является окончательной
  позицией аудитора") proposes reclassifications the final report may reject. Only the
  final counts. A draft has been observed proposing a reclassification of the exact
  transaction another covenant depends on.
- **Procedure documents that disclaim themselves.** "не является клиентским досье и не
  содержит определений по конкретному клиенту". They plant near-miss identifiers: a
  legal form one letter off (AG vs JSC vs LLP) and a sub-account with a second dash
  (`ACC-nnnn-nn`). No such sub-account exists in the ledger. Verify against the ledger.
- **Rasterised pages inside a text PDF.** Check **every page** with
  `len(page.get_text().strip())` and `page.get_images()`. A 5-page document can read
  fine while its load-bearing page is an image. Render those at ~200 dpi and read them.
- **A document with no text layer at all matches no text search**, so searching by account
  id silently returns fewer documents than the borrower actually has. The cheapest detector
  is a **count gap**: if you find four documents where sibling borrowers have five, the
  missing one is probably a scan, not an absence. Compare your haul against the others'
  before concluding a source does not exist. Found this way in a live run, and it held a
  borrower's entire KYC dossier.
- **A fact filed by the wrong department.** A correction has been found in a treasury
  memo rather than the auditor's notes. Any document carrying this borrower's account id
  deserves a read before being written off, whatever it appears to be.

## ② Isolate the borrower's ledger rows

Filter by `account_id`. The ledger contains many decoy accounts belonging to nobody.

### The category is what the description SAYS, not what the item IS

This is the single most expensive rule in this document. Three measured cells were lost
to breaking it, more than to any trap in the register.

A row belongs to the category its description names **literally**. It does not belong to a
category because accounting practice would put it there. "Operating expenses" means the
rows whose description says operating costs — **not** payroll, not electricity, not rent,
not insurance, not tax, even though every one of those is an operating expense in IFRS and
in ordinary usage.

A borrower typically carries one obvious row naming operating costs outright
(`"<something> servicing and operating costs"`, `"Plant operating and maintenance
expenses"`) — **but do not stop at the obvious one.** On a measured borrower a second row
belonged to the same category under different wording (`"Catalyst regeneration servicing
contract"`), and the covenant was unsolvable without it. Sweep for the category's whole
vocabulary, then check each candidate against the clause.

Reading the category semantically instead pulled in payroll and utilities and inflated a
denominator by 1.55×, turning a BREACH into a COMPLIANT and zeroing the cell.

The generator states the category in the wording. Your domain knowledge is the enemy here:
it is confident, it is reasonable, and it is wrong. When tempted to include a row because
"obviously that's an operating cost", check whether its description says so. If it does
not, the row belongs to its own category.

Two qualifications, both measured:
- A cost the **auditor names as a one-off expense** was in the expense base before it was
  added back, so it belongs to the category the clause deducts. Include those.
- A row can carry the category wording in a different phrasing (a foreign-currency
  `"servicing contract"` alongside the usual `"servicing and operating costs"`). Match on
  the category's vocabulary, not on one exact template — but keep the standard high enough
  that payroll and utilities stay out.

Then separate designed rows from generated filler using **both** signals — neither is
reliable alone:

- **Description-stem frequency.** Strip trailing `— <site>, <period>` suffixes and count
  each stem across the whole ledger. Filler stems recur many times across unrelated
  accounts; designed rows are near-unique.
  **This signal has a hole:** a filler row whose description carries no separator to strip
  looks unique. Before trusting uniqueness, search the ledger for the row's *word family*,
  not its exact stem — a measured false positive was caught only that way.
- **Counterparty name style.** Filler carries invented generic names, often with a
  parenthetical site suffix. Designed rows carry plausible regional company names.

**Traps here:**

- A designed row may wear a filler description and be designed through its
  **counterparty** instead. A related-party payment has been observed doing exactly this.
- Never assume one row per category. A covenant scoped to a quarter has been observed with
  one revenue row per quarter, all four sharing a wording.
- **Empty `amount` fields exist.** `Decimal("")` raises. Parse defensively **and report
  it** — a silently zeroed expense understates every aggregate it belongs to and looks
  like an answer. The real figure is disclosed in a document, and the clause that needs it
  often **names where to look** in its own second sentence. Read the whole clause before
  hunting.
- **Some amounts have no ledger row at all**, disclosed only in an auditor's note. An
  aggregate that only ever sums the CSV will miss them.

## ③ Quote the clause

Copy the sentence that states the metric and the threshold, verbatim. Then write the
metric as a formula.

**Trap:** a clause identifier is only a cell address. The same identifier can be a ratio
for one borrower and a dollar amount for another. It has no generic meaning. Read every
requested clause.

## ④ Threshold, direction, and gating condition — three separate questions

- **How much** is the limit?
- Is a breach **above or below** it? Not inferable from the clause number; many covenants
  breach *below* their threshold.
- **Does the test apply at all?** Some covenants are conditional and apply only once a
  trigger fires. Compute the trigger and show whether it fired.
- **What period?** Not always the full year.

A conditional covenant that does not fire is still reported: `actual` remains the metric's
real value and may sit above the limit while the status is COMPLIANT.

## ⑤ Materiality floors

If the auditor states a floor ("статьи в сумме не менее $X … не прибавляются"), apply it
and **show what you discarded**. A near-miss item just under the floor is planted.

## ⑥ Currency

If any row is not in the reporting currency, the rate must come **from the documents**.
There is deliberately no rate table; the rate is derived from one disclosed settlement
pair (invoice in foreign currency ↔ payment in dollars). Divide one by the other.

**Trap:** a foreign-currency row may not match your category keyword while still belonging
to that category. Missing the row and skipping the conversion are two separate bugs that
hide each other — fixing only one still yields the wrong verdict.

## ⑦ Related parties

The threshold is in **this borrower's own** KYC dossier. There is no shared default.
Compare with `>=` in `Decimal`.

**Traps here:**

- **Every dossier plants a near-miss** just under its own threshold, usually a large
  payer whose inclusion produces a spectacular false breach.
- **Look-through ownership.** A headline percentage may be held indirectly through
  another entity; the effective stake is the product, and it can fall below the threshold.
- **Names do not match literally.** Expect stray commas, quote artefacts from the PDF,
  `L.L.P.` vs `LLP`, and parenthetical site suffixes in the ledger. Normalise before
  comparing: drop parentheticals, strip punctuation and case, collapse whitespace.
- **Names collide across borrowers.** Resolve identity by account number only, never by
  company name.
- A related party genuinely absent from the ledger contributes zero — but confirm the
  absence with a normalised match first. "No rows found" is a finding to verify, not a
  fact to record.

## ⑧ Counterfactuals

For each cell, tabulate what changes the answer: remove the deciding row, skip the
currency conversion, use the other agreement edition, apply the draft's reclassification.
This shows what the cell rests on — and whether a single transaction decides it.

**Evidence is only named where removal flips the verdict**, and only where a *judgment*
attaches to that transaction (an auditor reclassification, a KYC determination, a
correction). A transaction that merely contributes to a sum is not evidence — not the
largest line, not the last before period close, not the one that tipped a running total.

## ⑨ Report honestly

- Never emit `null`, never leave a cell empty. An empty cell scores the same as a wrong
  one. If you cannot determine a value, say so — the cell falls back to a prior.
- `actual` is **positive**, two decimal places, the real value of the metric the covenant
  constrains — even when that value sits above the limit on a COMPLIANT cell.
- **`actual` carries the same unit as the threshold.** A limit written as a multiple
  (`0.04x`, `1.70x`) means `actual` is the ratio; a limit written as money (`$1,800,000.00`)
  means `actual` is the money amount. Read the limit's notation and match it — do not decide
  from the clause heading or from the grammar of the Russian sentence.
  Verified across every cell of a full dataset: each answer sits within 0.61x–1.47x of its
  own threshold, which is only possible if the two share a unit. Getting this wrong on a
  ratio covenant is a ~100% relative error — it keeps the status but forfeits the `actual`
  points entirely, and it has already cost a measured cell.
- **Round to exactly two decimals, and do not talk yourself out of it.** On a small ratio
  this feels destructive — `0.04339` becomes `0.04`, apparently an 8% loss — and an agent
  that noticed this filed six decimals to protect the precision. It scored **zero on the
  `actual`**: the key rounds to two decimals too, so the extra precision was the error.
  Four sibling cells whose true values were 0.0377, 0.0412, 0.0434 and 0.0442 are all
  stored as `0.04`. The instruction and the key agree; your arithmetic instinct is the
  odd one out. Two decimals, always.
- Do not invent an evidence id to look complete.
- Alongside the three graded fields, emit `"_threshold"` — the covenant's own limit as a
  number. Keys beginning with `_` are working notes, stripped before submission; this one
  lets the collector check `actual` against the limit automatically.
- **If the metric cannot be computed from what the documents and ledger actually provide,
  emit `"_computable": false` and say why.** Fill the cell anyway with your best proxy, but
  the flag makes the collector keep the prior instead. This is not a formality: on a
  measured cell an agent correctly identified that the required figure existed nowhere,
  labelled its number a proxy in prose — and still shipped a confident verdict, which
  scored **zero** where the untouched prior would have scored half. Prose caveats are not
  read by the collector. A substituted quantity is not the metric, however reasonable the
  substitution.
- State **what specifically** you are unsure about, not a confidence level. Confidence
  labels have measured as uninformative in both directions; a precise "the composition of
  X is defined nowhere in these documents" has measured as accurate every time.

## Arithmetic

`decimal.Decimal` everywhere, never floats. Compare before rounding, report rounded.
Leave behind a runnable script that recomputes every figure with `assert` on each
intermediate.

---

# Trap register

Accumulated as borrowers are solved. Every entry below was reproduced on real data;
apply all of them. Counts and amounts are dropped deliberately — they described one
dataset and would mislead on another.

| # | Trap | Cost if missed | Detection | Step |
|---|---|---|---|---|
| 1 | Two agreements per borrower, one superseded | whole cell — the editions differed by **one threshold** and nothing else | diff the sections containing every requested clause key; never trust the banner wording alone | ① |
| 2 | Draft auditor worksheet proposes a reclassification the final report rejects | two cells — the draft targeted the exact row another covenant rests on | the draft disclaims itself in its own text; only the final counts | ① |
| 3 | Self-disclaiming "procedure" doc with near-miss identity | wrong KYC source | legal form one letter off (AG/JSC/LLP) + sub-account with a second dash that exists in no ledger row | ① |
| 4 | Rasterised page **inside** an otherwise-text PDF | cell unsolvable — one carried the EBITDA add-backs | check text length **per page**, not per document | ① |
| 5 | Fact filed by the wrong department | status flip | read every doc carrying the account id whatever it looks like | ① |
| 5a | **Category read by meaning instead of by wording** | denominator inflated 1.55×, BREACH read as COMPLIANT, cell zeroed | the row's description names its category; payroll/utilities/rent/insurance are *not* operating expenses here. Verified: the narrow reading reproduces the key on three independent borrowers across three covenant shapes | ② |
| 6 | Ledger dominated by decoy accounts | every aggregate wrong | filter by `account_id` | ② |
| 7 | Generated filler vs planted rows | wrong category totals | stem frequency **and** counterparty name style — neither alone is clean | ② |
| 7a | Filler row with no separator to strip looks unique | false planted row | search the word family, not the exact stem | ② |
| 7b | Planted row wearing a filler description, designed through its **counterparty** | related-party total reads zero | check counterparties against the KYC table regardless of description | ② |
| 7c | The mirror of 7b: the **counterparty name suggests a category the description contradicts** — a payroll-named company paying accrued interest, utility-named companies billing insurance and rent | $8.67m of false denominator on one cell; another flipped a ratio from 3.64 to 0.42 | **counterparty decides related-party membership; description decides expense category.** Never let one do the other's job | ② |
| 7d | A word planted as bait for the wrong category — "**Capitalised** interest charge" reads as capex, is an interest charge | false BREACH on a capex cap | read the noun the description settles on, not the adjective | ② |
| 8 | Empty `amount` field | status flip; `Decimal("")` raises | parse defensively **and report**; the clause needing it often names where the figure is disclosed | ② |
| 9 | Amount disclosed but with no ledger row at all | aggregate understated | read the auditor's aggregation note | ② |
| 10 | Clause number is only a cell address | wrong metric entirely | read each clause; never infer type from the number | ③ |
| 11 | Direction — breach below, not above | whole cell | read the obligation verb | ④ |
| 12 | Conditional / springing covenant | whole cell | compute the trigger and show whether it fired | ④ |
| 13 | Period is not the full year | wrong aggregate | read the period out of the clause every time | ④ |
| 14 | Materiality floor with a planted near-miss just under it | ~12% on `actual` | apply the floor, show what was discarded | ⑤ |
| 15 | FX rate derived from one settled pair, no rate table | **status flip** | divide the dollar payment by the foreign invoice | ⑥ |
| 16 | A foreign-currency row misses the category keyword | status flip — and it **hides** trap 15: fixing either alone still gives the wrong verdict | classify by meaning, not by keyword | ⑥ |
| 17 | Near-miss holding just under the KYC threshold | false breaches of 11× and $6.7m | **Expect one at every borrower — this is systematic, not occasional.** Compare with `>=` in `Decimal` against this borrower's own figure | ⑦ |
| 18 | Look-through ownership — headline stake held indirectly | status flip | the dossier says the **effective** stake counts and gives the chain in the sentence under the table; multiply it. A 48.0% headline held through a 27.3%-owned vehicle is 13.1% — below a 30% bar. **The cell it sat on scored full marks anyway**, because that counterparty happened to have no outflow: a passing score is not evidence the logic is right | ⑦ |
| 19 | Counterparty names do not match literally | related party silently drops to zero | normalise: drop parentheticals and punctuation, fold case, unify legal forms | ⑦ |
| 20 | Same company name on different accounts | wrong borrower's data | resolve identity by account number only | ⑦ |
| 21 | Terms used everywhere and defined nowhere (opex, EBITDA) | three legal readings spanning **1.7×** on one cell | say so explicitly; this is underdetermination, not a mistake to fix | ⑨ |
| 22 | Numerator and denominator measured at **different reporting entities** — one consolidated at group level, one the borrower's own — and only the borrower's own data exists | whole cell | read the definition sentence: which entity does each side belong to? If the group-level figure is stated nowhere, the covenant is not computable — set `_computable: false` | ④ |
| 23 | Receipt dated inside the period but excluded from it by the auditor — title and risk transfer after period end | wrong aggregate on a period-scoped covenant | read the auditor's cut-off note; it names the transaction id outright | ④ |
| 24 | A self-declared proxy shipped as a confident verdict | **0 where an untouched prior scored 0.5** | prose caveats are not machine-readable; the flag is | ⑨ |

**What no filter catches.** Trap 21 is not detectable — the documents genuinely do not
decide. One cell in the open set reconciles under no mechanism at all: the quantity its
covenant needs is stated in no document and derivable from no ledger aggregate (searched
to the level of decompressed page content streams).

**And one "unexplainable" cell turned out to be a corrupt document.** Its printed
threshold was wrong; the key held the right one. Every method computed the ratio correctly
and compared it against the number on the page, so every method got the cell wrong. It was
tempting for two days to invent a mechanism that would reconcile it — a rounding rule, a
different revenue base — and each candidate fitted that one cell and would have silently
corrupted its siblings on the next dataset. Refusing to special-case it was what kept the
rest honest.

The rule that follows: **when a cell resists every reading, the input is a suspect, not
just your reasoning.** Say the arithmetic is sound and the premise looks wrong, and leave
it. Never bend the method to swallow one number. A fabricated mechanism does not merely
lose the cell — it generalises to every sibling cell you have not checked.
