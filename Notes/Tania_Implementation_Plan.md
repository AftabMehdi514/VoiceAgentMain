# Tania — Final Implementation Plan (Ready to Build)
don'te remove any functionliaty that is done previosuly-

## 2. Product-ID Hallucination Guard

**Problem:** nothing today stops the model returning a `product_id` in `items` that
doesn't exist in the catalog. `calculate_order_total`/`get_price` currently fall back to
a default `5.0` price for unrecognized IDs — the customer could be billed for a product
that isn't real.

**Behavior:** whenever a decision contains `items`, every `product_id` in the basket is
checked against the live, full active catalog (not just the top-6 cache used for the
system prompt). Any unrecognized ID is stripped from the basket — never priced with a
fallback default — and the model is told which product reference didn't match anything
real, so it can re-offer valid options instead of quietly proceeding with a phantom item.

**Status:** confirmed, to be built.

---

## 3. Total-Freshness Guard

**Problem:** if the customer changes quantity after `calculate_order_total` already ran,
nothing today forces a recalculation before `create_order` fires.

**Behavior:** the basket's contents are fingerprinted at the moment
`calculate_order_total` runs. Before `create_order` is allowed to execute, the current
basket is compared against that fingerprint. Any mismatch blocks the order-placement tool
call and forces `calculate_order_total` to run again first — same "can't skip ahead"
pattern as the existing address-before-payment guard.

**Status:** confirmed, to be built.

---

## 4. Payment-Method Guard — Intent Mapping + Enforcement

**Decision:** the LLM is responsible for *interpreting* what the customer means by
payment ("wallet," "online wallet," "card," "pay when it arrives," etc.) — the customer
should be free to describe it however they like, including just asking questions about
options first. What the model must *output*, whenever it sets `payment_method`, is
always exactly one of two literal values: `online` or `cash_on_delivery`.

**Two parts, working together:**
- **Prompt (small addition):** a couple of lines telling the model its job is to map
  customer phrasing to one of exactly two values, with a light example or two — not an
  exhaustive list of every possible phrasing (per your "less hardcoding into the prompt"
  preference).
- **Guard (enforcement):** the moment `payment_method` is non-null in a decision, it's
  checked against the two-value allow-list. Anything else — paraphrase, typo, stray
  value — is rejected before it ever reaches state; the field is cleared and the model is
  told its output wasn't one of the two valid options, prompting it to re-map or re-ask.

This keeps interpretation (a language task) on the LLM, and enforcement (a correctness
task) fully deterministic in code.

**Status:** confirmed, to be built.

---

## 5. Mobile Guard — Loose, With Open Tuning Flagged

**Decision:** not strict about exact length or format at this stage. The guard only
blocks clearly-not-a-phone-number input: contains letters, is empty, or is obviously too
short to be any kind of number attempt. Nothing stricter than that for now.

**Customer-facing behavior:** when rejected, the guard doesn't emit a fixed hardcoded
sentence itself. It tells the model *what specifically looked wrong* (e.g. "contains
non-numeric characters" / "doesn't look like a number at all"), and the model composes
the actual clarifying question in natural, bilingual-appropriate language — keeping
customer wording flexible rather than baked into the guard.

**Explicitly deferred, flagged in code as an open tuning point (not a gap to silently
forget):** exact Saudi format strictness (10-digit `05...` vs `+966` international form),
and how to handle near-miss cases (one digit short/long). Revisit once real customer
input patterns are visible.

**Status:** confirmed, to be built now in loose form; strict-format tuning deferred with
a comment marking it as pending review.

---

## 6. Quantity Guard

**Decided thresholds — building now:**
- **Zero** → rejected, re-ask. No legitimate reason a customer means this.
- **Negative** → rejected, re-ask.
- **Non-integer / not a real number** (covers vague input like "a few," "some," or any
  value that isn't a clean whole number) → rejected, re-ask for an exact quantity.

**Explicitly deferred (needs a real business number before it can be built correctly):**
an upper-bound "suspiciously large quantity" check (catching STT mishearing "2" as
"200"). Flagged in code as an open item — revisit once you have a real max-quantity
figure in mind (and whether it should vary by product type, e.g. gallons vs. cartons).

**Status:** zero/negative/non-integer confirmed, to be built. Upper-bound threshold
deferred, not forgotten.

---

## 7. Repeated Guard-Trip Escalation

**Decision:** no hardcoded scripted takeover message. Instead: track how many times each
*individual* guard fires within one turn (not just total iterations against the
`MAX_TOOL_ITERATIONS` cap). If the same guard fires a second time, the loop breaks out
*before* reaching the iteration cap and produces one final, still-LLM-composed reply that
explicitly asks the customer to reconfirm or restate the one specific piece of
information that keeps sticking — but the model isn't allowed to attempt another tool
call in that same exchange.

This avoids two failure modes at once: a stuck loop silently burning all iterations and
returning a possibly-broken decision to the customer, and a rigid fixed sentence that
would need separate hardcoded wording maintained per guard.

**Status:** confirmed, to be built.

---

## 8. DB-Down Guard

**Decision:** confirmed as originally proposed. Any tool result signaling a DB
connection failure is intercepted before the model's next reply is trusted — the model
is told the database is temporarily unavailable and should apologize and ask the
customer to try again shortly, rather than continuing the order flow as if a failed call
actually returned data.

**Status:** confirmed, to be built.

---

## 9. Product Search — Consolidation, Correction, and Two-Stage Matching

**Correction on record:** an earlier description called `product_search.py` a "fuzzy"
implementation versus a "basic" one in `tools.py`. That was inaccurate — neither file
currently does real fuzzy matching; both use plain SQL `LIKE`. The real problem is
**duplication**, not a basic-vs-advanced gap: `tools.py` has its own inline copy
(hardcoded `LIMIT 4`, no `order_count` field), separate from `product_search.py`'s
version, and they will keep drifting out of sync as one gets edited and the other
doesn't.

**Decision — single canonical implementation.** One `search_products` function, used
everywhere, replacing the duplicate inline copy in `tools.py`.

**Caching:** none. Every call queries the live catalog directly — confirmed appropriate
given the catalog's size (roughly 20-30 active products per `db_cache_products.md`), and
correctness matters more here than the small cost saved by caching, since a stale or
wrong result directly causes the customer to receive the wrong product.

**Two-stage matching — the actual mechanism, since the goal is landing the customer on
the product they truly meant despite STT/typing noise:**

- **Stage 1 — numeric hard filter.** If the customer's query contains digit sequences
  (e.g. "200" from "200ml bottles"), only products whose name/unit fields contain an
  *exactly matching* digit token survive this stage. This is a hard filter, not a scored
  one, specifically because "200ml" and "330ml" are one character apart as strings but
  are different physical products — fuzzy scoring alone could confidently return the
  wrong bottle size, so numeric equality is enforced before fuzzy logic ever runs. If the
  query has no digits at all, this stage is skipped and every candidate proceeds to Stage
  2.
- **Stage 2 — fuzzy scoring on the survivors.** Only among products that passed Stage 1
  (or the full set, if Stage 1 was skipped), fuzzy string similarity is applied against
  both English and Arabic name fields — this is what actually absorbs real-world noise
  like "galon" vs. "gallon" or word-order shuffles. Results are ranked by a combination of
  fuzzy score and real historical order popularity (same popularity-join logic already
  used elsewhere in the codebase).

**Numeric normalization — handled by the LLM, not a hardcoded dictionary.** Rather than
building a phrase-lookup mechanism to catch spoken/written number words ("two hundred"
vs "200"), this is handled with one **general** prompt instruction: the model normalizes
any number expressed in words (English or Arabic) into digits as part of how it
interprets customer input generally — covering item quantities and search queries alike,
not just search specifically. Example prompt line:

> *Whenever the customer expresses a number in words (in English or Arabic), convert it
> to digits before using it — in your response fields, item quantities, and any tool
> arguments. For example: "two hundred ml" → 200, "أربعة جالونات" → 4 gallons.*

**Why no code-side dictionary or guard behind this:** unlike payment method (which has a
hard 2-value enum guard catching any LLM slip), a search query is free text with no fixed
list to validate against — there's no equivalent guard possible here. The failure mode if
normalization is ever missed is graceful, not unsafe: Stage 1 simply finds no digits (same
as today's behavior) and the search falls through to fuzzy-only matching — the customer
still gets a real suggestion, just without the extra numeric protection on that one query.
This is an accepted, low-risk tradeoff, not an oversight.

**Weighted scoring — dropped.** An earlier version of this plan proposed weighting Stage
2's fuzzy score to favor numeric exactness over text similarity, to compensate for
uncertainty about whether Stage 1 would reliably have real digits to filter on. Since the
LLM is now expected to reliably normalize numbers before Stage 1 ever runs, that
uncertainty is resolved upstream — Stage 2 uses plain, standard fuzzy scoring (no special
weighting), evaluated during testing rather than pre-tuned in advance.

**Decimal handling — kept as a code-level fix, independent of the above.** Digit
extraction in Stage 1 treats a decimal number like "3.8" (from `Tania 3.8L`) as one token,
not two separate digits ("3" and "8") — this is a plain correctness fix in the extraction
logic itself, regardless of whether the number arrived as digits or was normalized from
words by the model.

**Status:** confirmed, to be built as described above.

---

## 10. Mobile / Identity Clearing — Consciously Deferred

**Clarified context:** this is a single-customer-per-web-session system, not a shared
multi-caller line — each session is already isolated to one customer. The cross-customer
identity-mixing scenario originally raised isn't the actual risk here, since a second
customer's order happens in an entirely separate session, not layered onto the first
customer's state.

**Decision:** no automatic "clear `customer_id`/`name` when `mobile` changes" guard is
being added. This is a deliberate, documented deferral, not an unnoticed hole. If real
usage later surfaces a case where a corrected/retyped mobile number within one session
ends up paired with a stale identity, that's the specific trigger to revisit this — it
would be a small, contained addition to `update_state` if it turns out to be needed.

**Status:** deferred by decision, documented for future reference only.

---

## 11. Partial Slot Cancellation — General `clear_slot` Intent

**Problem restated:** today the only special `intent` value is `"cancel"`, which wipes
the *entire* order. There's no way to clear just one slot — if a customer says "forget
that address" without immediately giving a replacement in the same breath, the old
address just sits in state, and could get read back to them at confirmation as if it
were still active.

**Decision — one general intent, not per-slot intents:** `intent: "clear_slot"` plus a
`target` field naming what to clear — `"address"`, `"payment_method"`, or
`"item:<product_id>"` for removing a single basket line. This covers all clearable slots
uniformly (per your confirmation that this should apply broadly, not just address).

**Why this was chosen over separate per-slot intents:** it scales without growing the
prompt every time a new clearable field is added later — only the *target* vocabulary
grows, not the intent space itself. It's also lower-risk to hand to the LLM than
per-slot intents would be: the model's only responsibility is naming *which* slot the
customer wants cleared. The actual nulling of that field, and the resulting `order_step`
rollback (e.g., clearing the address rolls the step back to `address_collection`), stays
fully deterministic in code — the model never decides *how* clearing behaves, only
*what* gets cleared.

**Not included:** clearing the `mobile`/identity fields through this mechanism — see
Section 10 above for why that's out of scope right now.

**Status:** confirmed, to be built.

---

## 12. Conversation History — No Truncation, No Summarization (For Now)

**Decision:** the full raw history keeps being sent every turn, unchanged. No
truncation, no pruning, no summarization logic is being built in this round.

**Explicitly deferred, not forgotten:** a per-conversation limit will eventually be
needed once real usage patterns (typical conversation length, actual token cost at
scale) are observable — but that's a decision for a later phase, sized against real data
rather than guessed in advance.

**Status:** deferred by decision — history stays untouched.

---

## 13. Debug Log — Enhanced, Not Removed

**Decision:** the existing `debug_log` in state is kept — not stripped out — but expanded
so it captures the full story of every turn, not just sparse entries. For each turn, the
log should record, in order:

- The **raw prompt sent to the model** for each LLM call within the turn (including
  retries triggered by guards).
- The **raw model response** for each of those calls, before JSON parsing.
- Which **guard(s) fired**, what they corrected, and on which iteration.
- Every **tool call** made — name, arguments, and the raw result returned.
- The **state diff** — what changed in state as a result of the turn (before/after key
  fields), not just the final state snapshot.
- The **final decision** and the reply actually sent to the customer.

**Purpose:** this turns the debug log into a genuine per-turn audit trail — enough to
reconstruct exactly why the agent said what it said, which guard(s) intervened, and what
the model actually saw and returned at each step, without needing to reproduce the
conversation to debug it after the fact.

**Status:** confirmed, to be built.

---

## Summary Table

| # | Item | Status |
|---|---|---|
| 1 | Think-mode (`/no_think` always) | No change — settled |
| 2 | Product-ID hallucination guard | Confirmed — build |
| 3 | Total-freshness guard | Confirmed — build |
| 4 | Payment-method intent mapping (prompt) + enum guard (code) | Confirmed — build |
| 5 | Mobile guard — loose non-numeric/empty check, LLM-composed clarification | Confirmed — build; strict-format tuning deferred |
| 6 | Quantity guard — zero/negative/non-integer | Confirmed — build; upper-bound threshold deferred |
| 7 | Repeated guard-trip escalation — soft break, LLM-composed reconfirmation | Confirmed — build |
| 8 | DB-down guard | Confirmed — build |
| 9 | Product search — single canonical function, no cache, two-stage numeric-then-fuzzy | Confirmed — build |
| 10 | Mobile/identity auto-clear | Deferred by decision — documented only |
| 11 | `clear_slot` general intent (address / payment_method / items) | Confirmed — build |
| 12 | History truncation/summarization | Deferred by decision — documented only |
| 13 | Debug log — enhanced full turn-by-turn audit trail (kept, not removed) | Confirmed — build |
| — | Address validation / 9-slot / geocoder | Untouched — remains archived |
| — | Automated test scripts | Out of scope — manual testing only, on request |
