import json
import re
import sys
import time
from core.llm_client import qwen_chat
from core.utils import detect_language, strip_cjk_leakage, parse_first_json
from core.prompts import ORDER_SYSTEM_PROMPT
from db.tools import execute_tool, check_active_products, build_basket_fingerprint
from db.sql_sandbox import load_relevant_ddl
from core.state import fresh_state, update_state, build_context, is_genuine_confirmation
from core.telemetry import emit

sys.stdout.reconfigure(encoding="utf-8")

print("Starting Tania agent — Local LLM Server (LM Studio).")

_RELEVANT_DDL = load_relevant_ddl()
SYSTEM_PROMPT = (
    ORDER_SYSTEM_PROMPT.rstrip()
    + "\n\n## Relevant DB Schema (write SELECT from user intent)\n"
    + _RELEVANT_DDL
    + "\n"
)

# Pack-size phrases: "20 bottles", "20 عبوة", "X 20", "× 20"
_PACK_SIZE_RE = re.compile(
    r"(?:(?:x|×)\s*(\d+)|(\d+)\s*(?:bottles?|bottle|عبوة|عبوات|pcs?|pieces?))",
    re.IGNORECASE,
)
_BOTTLE_HINT_RE = re.compile(r"\b(?:bottles?|عبوة|عبوات)\b", re.IGNORECASE)
_SKIP_CONFIRM_STEPS = ("address_collection", "payment", "confirmation", "done")
_CATALOG_TOOLS = frozenset({"query_catalog"})

# Product-discovery / filter signals (not dumped as prompt examples — used by guard only)
_PRODUCT_ASK_RE = re.compile(
    r"(?i)("
    r"\bproducts?\b|\bcatalog\b|\bavailable\b|\border\b|\bgallons?\b|\bcartons?\b|\bbottles?\b|"
    r"\bml\b|\bmils?\b|millilit|\bprice\b|\bcheap|\blowest\b|\bhighest\b|\bsuitable\b|"
    r"\bfamily\b|\bpeople\b|do you have|what (?:do|can) (?:you|i)|what(?:'s| is) available|"
    r"منتج|منتجات|جالون|كرتون|عبوة|سعر|أرخص|متوفر|عندكم|وش عند"
    r")"
)
_SELECTION_HINT_RE = re.compile(
    r"(?i)\b("
    r"add|take|want|put|basket|this one|that one|the first|the second|"
    r"أضف|ضيف|خذ|أبي|ابغى|هذا|هذي|الأول|الثاني"
    r")\b"
)
_FILTER_HINT_RE = re.compile(
    r"(?i)("
    r"\bcheap|\blowest\b|\bhighest\b|\bprice\b|\banother\b|\bother\b|\bavailable\b|"
    r"do you have|what (?:do|can) (?:you|i)|what(?:'s| is) available|"
    r"\bsuitable\b|\bfamily\b|\bpeople\b|how many|all products|"
    r"\bmils?\b|millilit|"
    r"أرخص|أغلى|سعر|آخر|غيره|متوفر|عندكم|ناس|عائلة|منتجات"
    r")"
)


def _extract_bottle_quantity(*texts) -> int | None:
    """Best-effort pack size from product name / customer utterance."""
    for text in texts:
        if not text:
            continue
        m = _PACK_SIZE_RE.search(str(text))
        if m:
            return int(m.group(1) or m.group(2))
    return None


def _utterance_requested_pack(customer_input: str) -> int | None:
    """If customer clearly asked for an N-bottle pack, return N."""
    if not customer_input:
        return None
    # Prefer patterns that mention bottles/عبوة so bare "20" (qty) is ignored
    if not _BOTTLE_HINT_RE.search(customer_input) and "×" not in customer_input and "x " not in customer_input.lower():
        # Still allow "20 bottles" style already covered; if no bottle word, skip
        # unless explicit X/× pack notation
        m_x = re.search(r"(?:x|×)\s*(\d+)", customer_input, re.IGNORECASE)
        if m_x:
            return int(m_x.group(1))
        return None
    return _extract_bottle_quantity(customer_input)


def normalize_catalog_offers(rows) -> list:
    """Normalize query_catalog rows into offer cache entries."""
    offers = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        pid = r.get("product_id")
        if pid is None:
            continue
        name_en = (r.get("name_en") or r.get("name") or "").strip()
        name_ar = (r.get("name_ar") or "").strip()
        unit_label = (
            r.get("unit_label")
            or r.get("unit_en")
            or r.get("unit")
            or ""
        )
        if isinstance(unit_label, dict):
            unit_label = unit_label.get("en") or unit_label.get("ar") or ""
        bottle_qty = r.get("bottle_quantity")
        if bottle_qty is None:
            bottle_qty = _extract_bottle_quantity(name_en, name_ar, str(unit_label))
        offers.append({
            "product_id": pid,
            "name_en": name_en,
            "name_ar": name_ar,
            "unit_label": str(unit_label).strip() if unit_label else "",
            "price_vat": r.get("price_vat"),
            "bottle_quantity": bottle_qty,
        })
    return offers


def cache_catalog_offers(state, rows) -> None:
    offers = normalize_catalog_offers(rows)
    if offers:
        state["_catalog_offers"] = offers
        state["_catalog_offers_at"] = time.time()


def selection_binding_guard(decision, state, customer_input: str):
    """
    Bind basket lines to exact offer rows; reject pack-size mismatches.
    Returns (ok, message) — message is set when guard should fire.
    """
    items = decision.get("items")
    if not items:
        return True, None

    offers = state.get("_catalog_offers") or []
    offer_by_id = {str(o["product_id"]): o for o in offers}
    requested_pack = _utterance_requested_pack(customer_input)

    bound = []
    for item in items:
        pid = item.get("product_id")
        if pid is None:
            continue
        pid_s = str(pid)
        offer = offer_by_id.get(pid_s)

        # Prefer offer-cache bind; fall back to active-product check later in flow
        if offer:
            name = offer.get("name_en") or offer.get("name_ar") or item.get("name")
            item = dict(item)
            item["product_id"] = offer["product_id"]
            item["name"] = name
            if offer.get("unit_label"):
                item["unit"] = offer["unit_label"]
            offer_pack = offer.get("bottle_quantity")
            if requested_pack is not None and offer_pack is not None and int(offer_pack) != int(requested_pack):
                # Try to find a better matching offer
                match = next(
                    (o for o in offers if o.get("bottle_quantity") is not None
                     and int(o["bottle_quantity"]) == int(requested_pack)),
                    None,
                )
                if match:
                    return False, (
                        f"Customer asked for a {requested_pack}-bottle pack but items used "
                        f"product_id={pid} ({offer_pack} bottles). Use product_id={match['product_id']} "
                        f"({match.get('name_en') or match.get('name_ar')}) from last_offers instead."
                    )
                return False, (
                    f"Customer asked for a {requested_pack}-bottle pack but product_id={pid} is "
                    f"{offer_pack} bottles. Pick the matching row from last_offers."
                )
            bound.append(item)
        else:
            # Not in last offers — keep for hallucinated_product_guard / DB check
            bound.append(item)

    decision["items"] = bound

    # Soft gate: new items this turn must not jump to address/payment/etc.
    had_items = bool(state.get("items"))
    items_confirmed = bool(state.get("_items_confirmed"))
    target_step = decision.get("order_step")
    if bound and not items_confirmed:
        # Treat as newly set if different from state or first set
        new_fp = json.dumps(bound, sort_keys=True, ensure_ascii=False)
        old_fp = json.dumps(state.get("items") or [], sort_keys=True, ensure_ascii=False)
        newly_set = (new_fp != old_fp) or not had_items
        if newly_set and target_step in _SKIP_CONFIRM_STEPS:
            decision["order_step"] = "product_selection"
            return False, (
                "Basket items were just set. Confirm the line item with the customer "
                "(name + qty) and stay on product_selection before collecting mobile/address."
            )
        if newly_set:
            decision["order_step"] = decision.get("order_step") or "product_selection"
            if decision["order_step"] in _SKIP_CONFIRM_STEPS:
                decision["order_step"] = "product_selection"

    return True, None


def _catalog_tool_ran(tool_trace) -> bool:
    return any((t.get("name") in _CATALOG_TOOLS) for t in (tool_trace or []))


def _is_selection_binding_utterance(customer_input: str, state) -> bool:
    """True when the customer is picking from last_offers, not asking a new catalog question."""
    text = (customer_input or "").strip()
    if not text:
        return False
    if not (state.get("_catalog_offers") or []):
        return False
    if _FILTER_HINT_RE.search(text):
        return False
    if is_genuine_confirmation(text, state.get("language", "ar")):
        return True
    if _utterance_requested_pack(text):
        return True
    if _SELECTION_HINT_RE.search(text) and len(text.split()) <= 12:
        return True
    return False


def needs_catalog_refresh(customer_input: str, state, tool_trace) -> bool:
    """New product discovery/filter ask must hit the catalog this turn."""
    if _catalog_tool_ran(tool_trace):
        return False
    if _is_selection_binding_utterance(customer_input, state):
        return False
    if not customer_input or not _PRODUCT_ASK_RE.search(customer_input):
        return False
    return True


def qwen_call(messages, max_new_tokens=700):
    for m in messages:
        if m["role"] == "system" and "/no_think" not in m["content"]:
            m["content"] += " /no_think"
            
    text = qwen_chat(messages, max_new_tokens=max_new_tokens)
        
    return strip_cjk_leakage(text)

MAX_TOOL_ITERATIONS = 4

def process_turn(customer_input, state, history):
    """
    Main turn handler.
    - Feeds the COMPLETE history to the LLM every turn.
    - Caches all tool results into state.
    - Appends the raw JSON decision to history (not plain text) to preserve JSON format.
    """
    context_json = build_context(state)

    # Build full message list: system + full history + current user turn with state
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history[:-1]:   # history already has the current user turn appended by caller — skip last
        messages.append(turn)
    # Final user message includes current state for context
    messages.append({
        "role": "user",
        "content": f"[Current state]\n{context_json}\n\n[Customer message]\n{customer_input}"
    })

    decision = None
    tool_trace = []
    guard_counts = {}
    guard_events = []

    def trigger_guard(guard_name, message, decision, rollback_step=None):
        guard_counts[guard_name] = guard_counts.get(guard_name, 0) + 1
        escalated = guard_counts[guard_name] >= 2
        guard_events.append({
            "name": guard_name,
            "message": message,
            "rollback_step": rollback_step,
            "count": guard_counts[guard_name],
            "escalated": escalated,
        })
        emit("guard", {
            "name": guard_name,
            "message": message,
            "rollback_step": rollback_step,
            "count": guard_counts[guard_name],
            "escalated": escalated,
        })
        decision["tool_call"] = None
        if rollback_step:
            decision["order_step"] = rollback_step
        messages.append({"role": "assistant", "content": _json_safe(decision)})
        if escalated:
            messages.append({
                "role": "user",
                "content": f"[SYSTEM Guard Escalation] You have repeatedly failed the '{guard_name}'. You MUST NOT call any tools this turn. Instead, explicitly ask the customer to reconfirm or correct the specific information that caused this."
            })
            return True  # Escalated
        else:
            messages.append({
                "role": "user",
                "content": f"[SYSTEM Guard] {message} Please correct your response."
            })
            return False

    for _ in range(MAX_TOOL_ITERATIONS + 1):
        raw = qwen_call(messages)
        decision = parse_first_json(raw)

        if not decision:
            decision = {
                "response": "عذراً، لم أفهم." if state.get("language") == "ar" else "Sorry, I did not catch that.",
                "tool_call": None,
                "order_step": state.get("order_step", "idle"),
            }
            break

        # ── State Engine Flow Guards (Anti-Hallucination) ─────────────────────
        target_step = decision.get("order_step")

        # 1. Address Guard
        if target_step in ("payment", "confirmation", "done") and not state.get("address_id"):
            trigger_guard("address_guard", "You cannot proceed to payment or confirmation without a confirmed address_id. You must use 'get_latest_address' or 'save_customer_and_address' first.", decision, "address_collection")
            continue

        # 2. Mandatory Fields Guard for create_order tool
        if decision.get("tool_call") and decision["tool_call"].get("name") == "create_order":
            missing = []
            items = decision.get("items") or state.get("items")
            payment = decision.get("payment_method") or state.get("payment_method")
            total = state.get("total_amount")
            
            if not items: missing.append("items")
            if not state.get("address_id"): missing.append("address_id")
            if not payment: missing.append("payment_method")
            if not total: missing.append("total_amount (call calculate_order_total)")
            
            if missing:
                rollback = "payment" if not payment else "confirmation"
                trigger_guard("create_order_guard", f"Cannot call create_order. Missing mandatory fields: {', '.join(missing)}. Please collect them or calculate the total first.", decision, rollback)
                continue

        # 3. Creation Guard
        if target_step == "done" and not state.get("temp_order_id"):
            tool_name = decision.get("tool_call", {}).get("name") if decision.get("tool_call") else None
            if tool_name != "create_order":
                trigger_guard("creation_guard", "You set order_step to 'done' but did not call 'create_order'. You must call 'create_order' to actually place the order in the database.", decision, "confirmation")
                continue

        # 4. Mobile Guard
        mobile = decision.get("mobile")
        if mobile is not None:
            mobile_str = str(mobile).strip()
            if not mobile_str or any(c.isalpha() for c in mobile_str) or len(mobile_str) < 5:
                # Open tuning point: Exact Saudi format strictness and near-miss cases
                trigger_guard("mobile_guard", "The mobile number provided is invalid (contains letters, is empty, or is too short). Ask the customer to provide a valid number.", decision)
                continue

        # 5. Quantity Guard
        items = decision.get("items")
        if items:
            invalid_qty = False
            for item in items:
                try:
                    qty = item.get("quantity")
                    if qty is None or float(qty) <= 0 or float(qty) != int(float(qty)):
                        invalid_qty = True
                except (ValueError, TypeError):
                    invalid_qty = True
            # Open tuning point: upper-bound 'suspiciously large quantity' check
            if invalid_qty:
                trigger_guard("quantity_guard", "One or more item quantities are zero, negative, or not a whole number. Ask the customer for an exact, valid quantity.", decision)
                continue

        # 6. Payment-Method Guard
        payment_method = decision.get("payment_method")
        if payment_method is not None and payment_method not in ("online", "cash_on_delivery"):
            decision["payment_method"] = None
            trigger_guard("payment_method_guard", "The payment_method must be exactly 'online' or 'cash_on_delivery'. Interpret the customer's intent and provide a valid value.", decision)
            continue

        # 7. Selection binding + Product-ID Hallucination Guard
        if items:
            ok_bind, bind_msg = selection_binding_guard(decision, state, customer_input)
            items = decision.get("items") or []
            if not ok_bind:
                trigger_guard(
                    "selection_binding_guard",
                    bind_msg,
                    decision,
                    "product_selection",
                )
                continue

            offers = state.get("_catalog_offers") or []
            offer_ids = {str(o["product_id"]) for o in offers}
            pids_to_check = [item.get("product_id") for item in items if item.get("product_id")]
            # Ids in last_offers are trusted for this turn; others must pass DB check
            need_db = [pid for pid in pids_to_check if str(pid) not in offer_ids]
            valid_pids = set(str(p) for p in offer_ids)
            if need_db:
                valid_pids |= {str(p) for p in check_active_products(need_db)}
            invalid_pids = [pid for pid in pids_to_check if str(pid) not in valid_pids]
            if invalid_pids:
                decision["items"] = [item for item in items if str(item.get("product_id")) in valid_pids]
                trigger_guard("hallucinated_product_guard", f"The following product IDs do not exist in the catalog: {invalid_pids}. They have been removed from the basket. Please offer valid products.", decision)
                continue

        # 8. Total-Freshness Guard
        tool_call = decision.get("tool_call")
        if tool_call and tool_call.get("name") == "create_order":
            current_fingerprint = build_basket_fingerprint(decision.get("items") or state.get("items"))
            if current_fingerprint != state.get("_total_fingerprint"):
                trigger_guard("freshness_guard", "The basket has changed since the last time calculate_order_total was called. You MUST call calculate_order_total again before create_order.", decision, "payment")
                continue

        # 8b. Catalog freshness — new product ask must query_catalog THIS turn
        tool_call = decision.get("tool_call")
        tool_name = (tool_call or {}).get("name") if tool_call else None
        if tool_name not in _CATALOG_TOOLS and needs_catalog_refresh(customer_input, state, tool_trace):
            trigger_guard(
                "catalog_freshness_guard",
                "This customer message is a product discovery/filter/availability question. "
                "The catalog is large — do not answer from memory or an older last_offers list. "
                "Call query_catalog with a narrow SELECT from the Relevant DB Schema THIS turn "
                "before naming any products or prices.",
                decision,
                "product_selection",
            )
            continue
        # ──────────────────────────────────────────────────────────────────────

        tool_call = decision.get("tool_call")
        if not tool_call:
            # Soft-confirm: if customer affirmed and basket already set, mark confirmed
            if (
                decision.get("items") or state.get("items")
            ) and is_genuine_confirmation(customer_input, state.get("language", "ar")):
                if decision.get("order_step") in (None, "product_selection", "idle") or state.get("items"):
                    decision["_items_confirmed"] = True
            break

        tool_name = tool_call.get("name")
        tool_args = tool_call.get("arguments") or {}

        tool_result = execute_tool(tool_name, tool_args)
        tool_trace.append({"name": tool_name, "arguments": tool_args, "result": tool_result})

        # 9. DB-Down Guard
        if isinstance(tool_result, dict) and tool_result.get("error") == "db_connection_failed":
            trigger_guard("db_down_guard", "The database is temporarily unavailable. Apologize to the customer and ask them to try again shortly.", decision)
            continue

        # ── Cache tool side-effects into state immediately ──────────────────
        if tool_name == "lookup_customer" and tool_result.get("found"):
            state["customer_id"] = tool_result.get("customer_id")
            state["name"] = tool_result.get("name")

        elif tool_name == "get_customer_orders":
            state["previous_orders"] = tool_result if isinstance(tool_result, list) else []

        elif tool_name == "query_catalog" and isinstance(tool_result, dict) and tool_result.get("rows"):
            cache_catalog_offers(state, tool_result["rows"])

        elif tool_name == "get_latest_address" and tool_result.get("found"):
            state["address"] = tool_result.get("address")
            state["address_id"] = tool_result.get("address_id")
            state["map_info"] = tool_result.get("map_info")

        elif tool_name == "save_customer_and_address" and tool_result.get("success"):
            state["temp_customer_id"] = tool_result.get("temp_customer_id")
            state["temp_address_id"] = tool_result.get("temp_address_id")
            state["address_id"] = tool_result.get("temp_address_id")
            state["map_info"] = tool_result.get("map_info")

        elif tool_name == "calculate_order_total":
            state["total_amount"] = tool_result.get("total_amount", 0.0)
            state["_total_fingerprint"] = build_basket_fingerprint(decision.get("items") or state.get("items"))
            state["_items_confirmed"] = True

        elif tool_name == "create_order" and tool_result.get("success"):
            state["temp_order_id"] = tool_result.get("temp_order_id")

        # Feed result back into message chain for next iteration
        messages.append({
            "role": "assistant",
            "content": _json_safe(decision),
        })
        messages.append({
            "role": "user",
            "content": (
                f"[Tool result for {tool_name}]\n{json.dumps(tool_result, ensure_ascii=False)}\n\n"
                f"Now produce the final JSON response to the customer. "
                f"Set tool_call to null unless a different tool is genuinely required next."
            )
        })

    # ── Confirmation guard ──────────────────────────────────────────────────
    if decision.get("order_step") == "done":
        if not is_genuine_confirmation(customer_input, state.get("language", "ar")):
            decision["order_step"] = "confirmation"

    decision["_tool_trace"] = tool_trace
    decision["_guard_events"] = guard_events

    # ── State Diff & Debug Log ──────────────────────────────────────────────
    old_state_snapshot = {k: v for k, v in state.items() if k != "debug_log"}

    state = update_state(state, decision)

    new_state_snapshot = {k: v for k, v in state.items() if k != "debug_log"}
    state_diff = {k: new_state_snapshot[k] for k in new_state_snapshot if new_state_snapshot[k] != old_state_snapshot.get(k)}
    decision["_state_diff"] = state_diff

    turn_log = {
        "customer_input": customer_input,
        "guard_triggers": guard_counts,
        "tool_trace": tool_trace,
        "state_diff": state_diff,
        "final_decision": {k: v for k, v in decision.items() if not str(k).startswith("_")},
        "messages_exchange": messages,
    }
    state["debug_log"].append(turn_log)

    agent_reply = decision.get(
        "response",
        "كيف أساعدك؟" if state.get("language") == "ar" else "How can I help you?"
    )
    return state, agent_reply, decision


def _json_safe(obj):
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)


def run_agent():
    state = fresh_state()
    history = []

    print("Tania Water Delivery — type quit to exit\n")

    while True:
        customer_input = input("Customer: ").strip()
        if customer_input.lower() in ("quit", "exit"):
            break
        if not customer_input:
            continue

        state["language"] = detect_language(customer_input)
        # Append to history BEFORE calling process_turn (so process_turn skips the last entry)
        history.append({"role": "user", "content": customer_input})

        # By default in CLI we use the local model
        state, agent_reply, decision = process_turn(customer_input, state, history)

        # Append the RAW JSON decision to history (not plain text) — preserves JSON format
        history.append({"role": "assistant", "content": _json_safe(decision)})

        tool_trace = decision.pop("_tool_trace", [])

        print(f"\n[Decision]\n{json.dumps(decision, ensure_ascii=False, indent=2)}")
        if tool_trace:
            print(f"\n[Tools]\n{json.dumps(tool_trace, ensure_ascii=False, indent=2)}")
        print(f"\n[State]\n{json.dumps(state, ensure_ascii=False, indent=2)}")
        print(f"\nAgent: {agent_reply}\n{'-'*60}")

        if state.get("order_step") == "done":
            print("\n ORDER COMPLETE\n")
            lang = state["language"]
            mobile = state["mobile"]
            state = fresh_state()
            state["language"] = lang
            state["mobile"] = mobile
            history = []


if __name__ == "__main__":
    run_agent()
