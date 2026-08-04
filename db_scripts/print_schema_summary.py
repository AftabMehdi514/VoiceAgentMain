import json
import os

path = os.path.join(os.path.dirname(__file__), "_schema_dump.json")
d = json.load(open(path, encoding="utf-8"))
tables = [
    "products", "categories", "products_in_category", "customers", "addresses",
    "orders", "order_items", "order_statuses", "deliveries", "delivery_trips",
    "trip_statuses", "payments", "sources", "channels",
    "tania_temp_customers", "tania_temp_addresses", "tania_temp_orders",
    "similar_products", "delivery_slots", "cancel_reasons", "address_types",
]
for t in tables:
    info = d["tables"][t]
    print("=" * 60)
    print(t, "rows", info["row_count"])
    for c in info["columns"]:
        print(f"  {c['name']:32} {c['type']:24} {c['key']:3} null={c['null']}")
    if info.get("sample"):
        print("SAMPLE:")
        for s in info["sample"][:2]:
            slim = {}
            for k, v in s.items():
                if isinstance(v, str) and len(v) > 100:
                    slim[k] = v[:100] + "…"
                else:
                    slim[k] = v
            print(" ", slim)
print("FKS:")
for fk in d.get("foreign_keys", []):
    print(" ", fk)
