import sys
sys.stdout.reconfigure(encoding="utf-8")

# 1. All modules import
from state import fresh_state, is_genuine_confirmation
from tools import execute_tool, get_top_products, search_products
from prompts import ORDER_SYSTEM_PROMPT
from tania_agent import process_turn
print("All modules import OK")

# 2. search_products works
r = search_products("200ml", limit=5)
print(f"search_products('200ml'): {len(r)} results, first product_id={r[0]['product_id'] if r else 'NONE'}")

r2 = search_products("gallon", limit=5)
print(f"search_products('gallon'): {len(r2)} results")

# 3. save_rating gracefully handles extra kwargs
result = execute_tool("save_rating", {"rating": 5, "mobile": "966533621446"})
print(f"save_rating with mobile kwarg: {result}")

# 4. search_products registered in TOOL_REGISTRY
result = execute_tool("search_products", {"query": "200ml", "limit": 3})
print(f"execute_tool search_products: {len(result)} results")

# 5. Products baked in prompt
prods = get_top_products()
assert prods[0]["name_en"] in ORDER_SYSTEM_PROMPT, "Products NOT in prompt!"
print(f"Product catalog in prompt: OK ({len(prods)} products)")
