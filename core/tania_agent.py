import json
import sys
from core.llm_client import qwen_chat
from core.utils import detect_language, strip_cjk_leakage, parse_first_json
from core.prompts import ORDER_SYSTEM_PROMPT
from db.tools import execute_tool, get_top_products, check_active_products, build_basket_fingerprint
from core.state import fresh_state, update_state, build_context, is_genuine_confirmation

sys.stdout.reconfigure(encoding="utf-8")

print("Starting Tania agent — Local LLM Server (LM Studio).")
# Warm up the product cache at startup so it is never fetched mid-conversation
_startup_products = get_top_products()
print(f"Product catalog cached: {len(_startup_products)} products loaded at startup.")

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
    messages = [{"role": "system", "content": ORDER_SYSTEM_PROMPT}]
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

    def trigger_guard(guard_name, message, decision, rollback_step=None):
        guard_counts[guard_name] = guard_counts.get(guard_name, 0) + 1
        decision["tool_call"] = None
        if rollback_step:
            decision["order_step"] = rollback_step
        messages.append({"role": "assistant", "content": _json_safe(decision)})
        if guard_counts[guard_name] >= 2:
            messages.append({
                "role": "user",
                "content": f"[SYSTEM Guard Escalation] You have repeatedly failed the '{guard_name}'. You MUST NOT call any tools this turn. Instead, explicitly ask the customer to reconfirm or correct the specific information that caused this."
            })
            return True # Escalated
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

        # 7. Product-ID Hallucination Guard
        if items:
            pids_to_check = [item.get("product_id") for item in items if item.get("product_id")]
            valid_pids = check_active_products(pids_to_check)
            invalid_pids = [pid for pid in pids_to_check if pid not in valid_pids]
            if invalid_pids:
                decision["items"] = [item for item in items if item.get("product_id") in valid_pids]
                trigger_guard("hallucinated_product_guard", f"The following product IDs do not exist in the catalog: {invalid_pids}. They have been removed from the basket. Please offer valid products.", decision)
                continue

        # 8. Total-Freshness Guard
        tool_call = decision.get("tool_call")
        if tool_call and tool_call.get("name") == "create_order":
            current_fingerprint = build_basket_fingerprint(decision.get("items") or state.get("items"))
            if current_fingerprint != state.get("_total_fingerprint"):
                trigger_guard("freshness_guard", "The basket has changed since the last time calculate_order_total was called. You MUST call calculate_order_total again before create_order.", decision, "payment")
                continue
        # ──────────────────────────────────────────────────────────────────────

        tool_call = decision.get("tool_call")
        if not tool_call:
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

        elif tool_name in ("get_products", "get_top_products") and isinstance(tool_result, list):
            # Already cached at module level — just ensure state reflects it
            pass

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

    # ── State Diff & Debug Log ──────────────────────────────────────────────
    old_state_snapshot = {k: v for k, v in state.items() if k != "debug_log"}
    
    state = update_state(state, decision)

    new_state_snapshot = {k: v for k, v in state.items() if k != "debug_log"}
    state_diff = {k: new_state_snapshot[k] for k in new_state_snapshot if new_state_snapshot[k] != old_state_snapshot.get(k)}
    
    turn_log = {
        "customer_input": customer_input,
        "guard_triggers": guard_counts,
        "tool_trace": tool_trace,
        "state_diff": state_diff,
        "final_decision": decision,
        "messages_exchange": messages  # Full prompt and response exchange this turn
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
