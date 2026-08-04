"""
Smoke: demand advice binding — offer cache, pack-size bind, browse via query_catalog.
Does not require the LLM; exercises catalog tools + selection_binding_guard.
"""
import json
from core.state import fresh_state, build_context, update_state
from core.tania_agent import (
    normalize_catalog_offers,
    cache_catalog_offers,
    selection_binding_guard,
    _utterance_requested_pack,
    needs_catalog_refresh,
)
from db.tools import query_catalog, execute_tool
from core.prompts import ORDER_SYSTEM_PROMPT

# ── Prompt contract ─────────────────────────────────────────────────────────
assert "get_top_products" not in ORDER_SYSTEM_PROMPT
assert "ALWAYS call get_top_products" not in ORDER_SYSTEM_PROMPT
assert "query_catalog" in ORDER_SYSTEM_PROMPT
assert "LARGE" in ORDER_SYSTEM_PROMPT.upper() or "large" in ORDER_SYSTEM_PROMPT
assert "SELECT-only" in ORDER_SYSTEM_PROMPT or "SELECT only" in ORDER_SYSTEM_PROMPT or "mutating" in ORDER_SYSTEM_PROMPT.lower()
assert "Tool discipline" in ORDER_SYSTEM_PROMPT
assert "Catalog intelligence" in ORDER_SYSTEM_PROMPT
assert "product_id=20017" not in ORDER_SYSTEM_PROMPT
assert "last_offers" in ORDER_SYSTEM_PROMPT
print("PROMPT_OK")

assert needs_catalog_refresh("do you have mil product?", fresh_state(), []) is True
assert needs_catalog_refresh("what is the lowest price?", fresh_state(), []) is True
assert needs_catalog_refresh("what products do you have?", fresh_state(), []) is True
st = fresh_state()
cache_catalog_offers(st, [{"product_id": 1, "name_en": "Gallon", "price_vat": 8.75}])
assert needs_catalog_refresh("yes", st, []) is False
assert needs_catalog_refresh("add that", st, []) is False
assert needs_catalog_refresh("do you have mil?", st, [{"name": "query_catalog"}]) is False
print("CATALOG_FRESHNESS_OK")

# LLM must not be able to call legacy catalog tools
assert execute_tool("get_top_products", {}).get("error", "").startswith("unknown_tool")
assert execute_tool("get_products", {}).get("error", "").startswith("unknown_tool")
assert execute_tool("search_products", {"query": "200"}).get("error", "").startswith("unknown_tool")
print("LEGACY_TOOLS_UNREGISTERED_OK")

# ── Browse = query_catalog open sample into offer cache ─────────────────────
state = fresh_state()
sql_browse = (
    "SELECT product_id, product_name, unit, price_vat FROM products "
    "WHERE status=1 AND show_in_oms=1 AND deleted_at IS NULL AND price_vat > 0 "
    "ORDER BY product_sort LIMIT 5"
)
browse = query_catalog(sql_browse)
assert not browse.get("error"), browse
assert browse.get("count", 0) >= 1, browse
cache_catalog_offers(state, browse["rows"])
assert len(state["_catalog_offers"]) >= 1
ctx = json.loads(build_context(state))
assert "last_offers" in ctx and len(ctx["last_offers"]) >= 1
assert ctx["last_offers"][0]["product_id"] == state["_catalog_offers"][0]["product_id"]
print("BROWSE_CACHE_OK", len(state["_catalog_offers"]), ctx["last_offers"][0].get("name_en"))

# Prior search list overwritten by new browse query cache
cache_catalog_offers(
    state,
    [{"product_id": 99999, "name_en": "Fake 200ml search only", "price_vat": 1.0}],
)
assert state["_catalog_offers"][0]["product_id"] == 99999
cache_catalog_offers(state, browse["rows"])
assert state["_catalog_offers"][0]["product_id"] != 99999
print("BROWSE_REFRESH_OK", state["_catalog_offers"][0]["product_id"])

# ── Family / land demand → query_catalog gallons ────────────────────────────
sql_g = (
    "SELECT product_id, product_name, unit, price_vat FROM products "
    "WHERE status=1 AND show_in_oms=1 AND deleted_at IS NULL "
    "AND price_vat > 0 "
    "AND (product_name LIKE '%gallon%' OR product_name LIKE '%Gallon%' OR unit LIKE '%Gallon%') "
    "LIMIT 5"
)
r = query_catalog(sql_g)
assert not r.get("error"), r
assert r.get("count", 0) >= 1, r
cache_catalog_offers(state, r["rows"])
ctx = json.loads(build_context(state))
assert any("gallon" in (o.get("name_en") or "").lower() or "gallon" in (o.get("unit_label") or "").lower()
           for o in ctx["last_offers"]), ctx["last_offers"]
print("FAMILY_DEMAND_TOOL_OK", r["count"], ctx["last_offers"][0])

# ── Cheapest rank ──────────────────────────────────────────────────────────
sql_cheap = (
    "SELECT product_id, product_name, unit, price_vat FROM products "
    "WHERE status=1 AND show_in_oms=1 AND deleted_at IS NULL AND price_vat > 0 "
    "ORDER BY price_vat ASC LIMIT 5"
)
r_cheap = query_catalog(sql_cheap)
assert not r_cheap.get("error"), r_cheap
assert r_cheap.get("count", 0) >= 1, r_cheap
print("CHEAPEST_OK", r_cheap["rows"][0].get("price_vat"), r_cheap["rows"][0].get("name_en"))

# ── Pack-size utterance parsing ─────────────────────────────────────────────
assert _utterance_requested_pack("I'll take the 20 bottles") == 20
assert _utterance_requested_pack("20 عبوة") == 20
assert _utterance_requested_pack("yes add two") is None
print("PACK_PARSE_OK")

# ── 20-bottle bind must not accept 24-pack id ───────────────────────────────
offers = normalize_catalog_offers([
    {
        "product_id": 20017,
        "name_en": "Tania 200ml X 24 Bottles",
        "name_ar": "تانيا 200 مل × 24 عبوة",
        "unit_en": "Carton",
        "price_vat": 11.07,
    },
    {
        "product_id": 20020,
        "name_en": "Tania 200ml X 20 Bottles",
        "name_ar": "تانيا 200 مل × 20 عبوة",
        "unit_en": "Carton",
        "price_vat": 10.0,
    },
])
assert offers[0]["bottle_quantity"] == 24
assert offers[1]["bottle_quantity"] == 20

state = fresh_state()
state["_catalog_offers"] = offers

decision = {
    "response": "Adding 24 pack",
    "items": [{"product_id": 20017, "quantity": 1, "name": "20 bottles"}],
    "order_step": "product_selection",
    "tool_call": None,
}
ok, msg = selection_binding_guard(decision, state, "I want the 20 bottles please")
assert ok is False, (ok, msg)
assert "20" in (msg or "") and "20020" in (msg or ""), msg
print("BIND_REJECT_24_OK", msg)

decision2 = {
    "response": "Adding 20 pack",
    "items": [{"product_id": 20020, "quantity": 1, "name": "whatever wrong name"}],
    "order_step": "product_selection",
    "tool_call": None,
}
ok2, msg2 = selection_binding_guard(decision2, state, "I want the 20 bottles please")
assert ok2 is True, msg2
assert decision2["items"][0]["name"] == "Tania 200ml X 20 Bottles"
assert decision2["items"][0]["product_id"] == 20020
print("BIND_20_OK", decision2["items"][0])

# ── Soft gate: new items cannot jump to address ─────────────────────────────
state3 = fresh_state()
state3["_catalog_offers"] = offers
decision3 = {
    "response": "What's your mobile?",
    "items": [{"product_id": 20020, "quantity": 1, "name": "x"}],
    "order_step": "address_collection",
    "tool_call": None,
}
ok3, msg3 = selection_binding_guard(decision3, state3, "yes add the 20 bottles")
assert ok3 is False, (ok3, msg3)
assert decision3["order_step"] == "product_selection"
print("CONFIRM_BEFORE_MOBILE_OK", msg3)

state3["_items_confirmed"] = True
state3["items"] = [{"product_id": 20020, "quantity": 1, "name": "Tania 200ml X 20 Bottles"}]
decision4 = {
    "response": "Mobile?",
    "items": state3["items"],
    "order_step": "address_collection",
    "tool_call": None,
}
ok4, msg4 = selection_binding_guard(decision4, state3, "yes")
assert ok4 is True, msg4
print("CONFIRMED_ADVANCE_OK")

update_state(state3, {"items": [{"product_id": 20020, "quantity": 2, "name": "Tania 200ml X 20 Bottles"}]})
assert state3["_items_confirmed"] is False
print("SMOKE_DEMAND_BIND_OK")
