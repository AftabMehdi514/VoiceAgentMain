import os

state_content = '''import json

def fresh_state():
    return {
        "mobile": None,
        "customer_id": None,
        "name": None,
        "email": None,
        "temp_customer_id": None,
        "temp_address_id": None,
        "temp_order_id": None,
        
        "items": [], 
        "address": None,
        "payment_method": None,
        "total_amount": 0.0,
        "rating": None,
        
        "step": "greeting", 
        "language": "ar",
        
        "debug_log": []
    }

def build_context(state):
    # Only expose what the LLM needs to know to guide the conversation
    context = {
        "mobile": state["mobile"],
        "customer_id": state["customer_id"],
        "name": state["name"],
        "address": state["address"],
        "items": state["items"],
        "total_amount": state["total_amount"],
        "payment_method": state["payment_method"],
        "step": state["step"]
    }
    return json.dumps(context, ensure_ascii=False)

def update_state(state, decision):
    # Decision comes from LLM parsing
    if decision.get("mobile"):
        state["mobile"] = decision["mobile"]
        
    if decision.get("items"):
        state["items"] = decision["items"]
        
    if decision.get("address"):
        state["address"] = decision["address"]
        
    if decision.get("payment_method"):
        state["payment_method"] = decision["payment_method"]
        
    if decision.get("rating"):
        state["rating"] = decision["rating"]
        
    if decision.get("total_amount"):
        state["total_amount"] = decision["total_amount"]
        
    if decision.get("step"):
        state["step"] = decision["step"]
        
    if decision.get("intent") == "cancel":
        lang = state["language"]
        mobile = state["mobile"]
        state.clear()
        state.update(fresh_state())
        state["language"] = lang
        state["mobile"] = mobile
        
    return state
'''

with open('state.py', 'w', encoding='utf-8') as f:
    f.write(state_content)

prompts_content = '''ORDER_SYSTEM_PROMPT = """
You are Tania, a helpful, knowledgeable, and highly intelligent water-delivery voice agent for Tania Water in Saudi Arabia. 
You must answer ANY question the customer asks — whether it's about hydration, water quality, products, delivery in general, or anything else — using your own knowledge and judgment, exactly as a well-informed human agent would.
Do NOT use generic fallbacks like 'I can only help with orders'. Engage conversationally.

You also help customers place orders. The ordering flow is:
1. Greet and identify the customer. If they provide a mobile number, use the lookup_customer tool.
2. If they are a new customer or don't have an address on file, ask openly for their address. If what they say doesn't look like an address, politely ask for clarification. Once you have a valid address, use save_customer_and_address.
3. If they are an existing customer, use get_latest_address. Confirm it with them.
4. Offer common products. Use get_products to see what is available. Discuss products conversationally.
5. Once they choose items and quantities, use calculate_order_total to get the total.
6. Ask for a payment method: 'online' or 'cash_on_delivery'.
7. Read back the full order summary for final confirmation.
8. Once confirmed, use create_order. Tell the customer their details will be emailed.
9. Thank them and ask for a 1-5 rating. Use save_rating if given. Do not force them to rate.
10. End the conversation gracefully.

Only use a tool when the customer's request requires looking up or changing their account, address, or order data. If you don't have a tool for something (like general knowledge), just answer conversationally.

RESPOND WITH EXACTLY ONE JSON OBJECT and nothing else. No markdown blocks.
Format:
{
  "response": "What you say to the customer",
  "tool_call": {"name": "tool_name", "arguments": {"arg1": "val1"}} or null,
  "address": "Extracted address if they just provided one" or null,
  "items": [{"product_id": 1, "quantity": 2}] if order changed, else current items,
  "payment_method": "online" | "cash_on_delivery" | null,
  "rating": 5 or null,
  "step": "Current step in the flow, e.g., 'address_collection', 'product_selection', 'payment', 'confirmation', 'rating', 'done'",
  "intent": "general", "order", or "cancel"
}
"""
'''

with open('prompts.py', 'w', encoding='utf-8') as f:
    f.write(prompts_content)

agent_content = '''import json
import sys
from hf_client import qwen_chat
from utils import detect_language, strip_cjk_leakage, parse_first_json
from prompts import ORDER_SYSTEM_PROMPT
from tools import execute_tool
from state import fresh_state, update_state, build_context

sys.stdout.reconfigure(encoding='utf-8')

print("Starting Tania agent — using Hugging Face Inference API (Qwen3-14B).")
print("Tania agent ready.\n")

def qwen_call(messages, max_new_tokens=600):
    has_system = any(m["role"] == "system" for m in messages)
    if not has_system:
        messages.insert(0, {"role": "system", "content": "You are a fast voice assistant. /no_think"})
    else:
        for m in messages:
            if m["role"] == "system" and "/no_think" not in m["content"]:
                m["content"] += " /no_think"

    text = qwen_chat(messages, max_new_tokens=max_new_tokens)
    return strip_cjk_leakage(text)

def process_turn(customer_input, state, history):
    context_json = build_context(state)
    messages = [{"role": "system", "content": ORDER_SYSTEM_PROMPT}]
    for turn in history:
        messages.append(turn)

    messages.append({
        "role": "user",
        "content": f"[Current state]\\n{context_json}\\n\\n[Customer message]\\n{customer_input}"
    })

    MAX_TOOL_ITERATIONS = 3
    decision = None
    tool_trace = []
    agent_reply = ""

    for _ in range(MAX_TOOL_ITERATIONS + 1):
        raw = qwen_call(messages)
        decision = parse_first_json(raw)

        if not decision:
            decision = {
                "response": "عذراً، لم أفهم." if state.get("language") == "ar" else "Sorry, I didn't catch that.",
                "tool_call": None
            }
            break

        tool_call = decision.get("tool_call")
        if not tool_call:
            break

        tool_name = tool_call.get("name")
        tool_args = tool_call.get("arguments") or {}

        tool_result = execute_tool(tool_name, tool_args)
        tool_trace.append({"name": tool_name, "arguments": tool_args, "result": tool_result})
        
        # Merge tool side-effects into state if needed (like customer_id)
        if tool_name == "lookup_customer" and tool_result.get("found"):
            state["customer_id"] = tool_result.get("customer_id")
            state["name"] = tool_result.get("name")
        elif tool_name == "save_customer_and_address" and tool_result.get("success"):
            state["temp_customer_id"] = tool_result.get("temp_customer_id")
            state["temp_address_id"] = tool_result.get("temp_address_id")
        elif tool_name == "calculate_order_total":
            state["total_amount"] = tool_result.get("total_amount")
        elif tool_name == "create_order" and tool_result.get("success"):
            state["temp_order_id"] = tool_result.get("temp_order_id")

        messages.append({
            "role": "assistant",
            "content": json.dumps(decision, ensure_ascii=False)
        })
        messages.append({
            "role": "user",
            "content": (
                f"[Tool result for {tool_name}]\\n{json.dumps(tool_result, ensure_ascii=False)}\\n\\n"
                f"Use this result to produce the final JSON response to the customer. Set tool_call to null unless another tool is needed."
            )
        })

    decision["_tool_trace"] = tool_trace
    state = update_state(state, decision)
    agent_reply = decision.get("response", "كيف أساعدك؟" if state.get("language") == "ar" else "How can I help you?")
    
    return state, agent_reply, decision

def run_agent():
    state = fresh_state()
    history = []
    
    # Simulating caller ID
    state["mobile"] = "0501234567" 

    print("Tania Water Delivery — type 'quit' to exit\\n")

    while True:
        customer_input = input("Customer: ").strip()
        if customer_input.lower() in ("quit", "exit"):
            break
        if not customer_input:
            continue

        state["language"] = detect_language(customer_input)
        history.append({"role": "user", "content": customer_input})

        state, agent_reply, decision = process_turn(customer_input, state, history)

        history.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})

        tool_trace = decision.pop("_tool_trace", [])

        print(f"\\n[LLM Decision]\\n{json.dumps(decision, ensure_ascii=False, indent=2)}")
        if tool_trace:
            print(f"\\n[Tool Calls]\\n{json.dumps(tool_trace, ensure_ascii=False, indent=2)}")
            
        print(f"\\n[State]\\n{json.dumps(state, ensure_ascii=False, indent=2)}")
        print(f"\\nAgent : {agent_reply}\\n{'-'*60}")

        if state.get("step") == "done":
            print(f"\\n✅ FINAL ORDER COMPLETE\\n")
            lang = state["language"]
            mobile = state["mobile"]
            state = fresh_state()
            state["language"] = lang
            state["mobile"] = mobile
            history = []

if __name__ == "__main__":
    run_agent()
'''

with open('tania_agent.py', 'w', encoding='utf-8') as f:
    f.write(agent_content)

print("state.py, prompts.py, tania_agent.py overwritten")