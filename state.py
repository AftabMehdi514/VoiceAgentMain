import re
import json

# Confirmation helpers (restored from archive)
CONFIRMATION_WORDS_AR = {
    "نعم", "أيوه", "إيه", "آيوه", "أوكي", "اوكي", "تمام", "زين",
    "ماشي", "صح", "صحيح", "أكيد", "بالظبط", "عدل", "موافق", "يلا",
    "سليم", "كويس", "طيب", "ممتاز", "مضبوط", "اتفقنا", "خلاص", "أكمل",
}
CONFIRMATION_WORDS_EN = {
    "yes", "yep", "yeah", "correct", "right", "sure", "ok", "okay",
    "confirmed", "proceed", "perfect", "great",
}
CONFIRMATION_PHRASES_EN = ("sounds good", "that is right", "go ahead", "do it")

def is_genuine_confirmation(customer_message: str, lang: str) -> bool:
    if not customer_message:
        return False
    lowered = customer_message.lower()
    if lang == "ar":
        tokens = re.findall(r"[\u0600-\u06FF]+", customer_message)
        return any(tok in CONFIRMATION_WORDS_AR for tok in tokens)
    tokens = re.findall(r"[a-zA-Z]+", lowered)
    if any(tok in CONFIRMATION_WORDS_EN for tok in tokens):
        return True
    return any(phrase in lowered for phrase in CONFIRMATION_PHRASES_EN)

def fresh_state():
    return {
        "mobile": None,
        "customer_id": None,
        "name": None,
        "email": None,
        "temp_customer_id": None,
        "address": None,
        "address_id": None,
        "map_info": None,
        "items": [],
        "total_amount": 0.0,
        "payment_method": None,
        "temp_address_id": None,
        "temp_order_id": None,
        "previous_orders": [],
        "order_step": "idle",
        "language": "ar",
        "debug_log": []
    }

def build_context(state):
    context = {
        "mobile": state.get("mobile"),
        "customer_id": state.get("customer_id"),
        "name": state.get("name"),
        "order_step": state.get("order_step", "idle"),
        "address": state.get("address"),
        "address_id": state.get("address_id"),
        "map_info": state.get("map_info"),
        "items": state.get("items", []),
        "total_amount": state.get("total_amount", 0.0),
        "payment_method": state.get("payment_method"),
        "previous_orders": state.get("previous_orders", []),
    }
    return json.dumps(context, ensure_ascii=False)

def update_state(state, decision):
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
    if decision.get("order_step"):
        state["order_step"] = decision["order_step"]
    if decision.get("intent") == "cancel":
        lang = state.get("language", "ar")
        mobile = state.get("mobile")
        customer_id = state.get("customer_id")
        name = state.get("name")
        state.clear()
        state.update(fresh_state())
        state["language"] = lang
        state["mobile"] = mobile
        state["customer_id"] = customer_id
        state["name"] = name
    elif decision.get("intent") == "clear_slot":
        target = decision.get("target")
        if target == "address":
            state["address"] = None
            state["address_id"] = None
            state["map_info"] = None
            state["temp_address_id"] = None
            if state["order_step"] in ("payment", "confirmation", "done"):
                state["order_step"] = "address_collection"
        elif target == "payment_method":
            state["payment_method"] = None
            if state["order_step"] in ("confirmation", "done"):
                state["order_step"] = "payment"
        elif target and target.startswith("item:"):
            try:
                pid = str(target.split("item:")[1])
                state["items"] = [item for item in state.get("items", []) if str(item.get("product_id")) != pid]
                state["total_amount"] = 0.0 # Will force recalculation
            except Exception:
                pass
    return state
