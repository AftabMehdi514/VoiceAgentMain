"""Dump curated column lists + lookup samples to UTF-8 text."""
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from db.db import get_connection

dump_path = os.path.join(os.path.dirname(__file__), "_schema_dump.json")
out_path = os.path.join(os.path.dirname(__file__), "_schema_summary.txt")
d = json.load(open(dump_path, encoding="utf-8"))

tables = [
    "products", "categories", "products_in_category", "similar_products",
    "customers", "addresses", "address_types",
    "orders", "order_items", "order_statuses", "payments", "sources", "channels",
    "deliveries", "delivery_trips", "trip_statuses", "delivery_slots",
    "cancel_reasons", "tania_temp_customers", "tania_temp_addresses", "tania_temp_orders",
    "coupons", "promocodes", "tickets",
]

lines = []
for t in tables:
    info = d["tables"][t]
    lines.append("=" * 72)
    lines.append(f"{t}  rows={info['row_count']}  cols={len(info['columns'])}")
    for c in info["columns"]:
        lines.append(
            f"  {c['name']:32} {c['type']:24} key={c['key'] or '-':3} null={c['null']}"
        )
    if info.get("sample"):
        lines.append("SAMPLE:")
        for s in info["sample"][:2]:
            lines.append("  " + json.dumps(s, ensure_ascii=False))
    lines.append("")

lines.append("FOREIGN KEYS (among candidates):")
for fk in d.get("foreign_keys", []):
    lines.append(f"  {fk['table']}.{fk['column']} -> {fk['ref_table']}.{fk['ref_column']}")

conn = get_connection()
cur = conn.cursor(dictionary=True)

def q(title, sql):
    lines.append("")
    lines.append(f"LOOKUP: {title}")
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        for r in rows[:30]:
            # stringify
            clean = {k: (None if v is None else str(v)[:200]) for k, v in r.items()}
            lines.append("  " + json.dumps(clean, ensure_ascii=False))
    except Exception as e:
        lines.append(f"  ERROR: {e}")

# Discover status-like columns via data
q("order_statuses all", "SELECT * FROM order_statuses LIMIT 20")
q("trip_statuses all", "SELECT * FROM trip_statuses LIMIT 20")
q("sources all", "SELECT * FROM sources LIMIT 20")
q("channels sample", "SELECT * FROM channels LIMIT 10")
q("categories all", "SELECT * FROM categories")
q("address_types all", "SELECT * FROM address_types")
q("cancel_reasons sample", "SELECT * FROM cancel_reasons LIMIT 10")
q("tania_temp_orders all", "SELECT * FROM tania_temp_orders")
q(
    "orders recent slim",
    """SELECT order_id, customer_id, address_id, order_status_id, source_id,
              total, grand_total, payment_method, payment_status, created_at, deleted_at
       FROM orders ORDER BY order_id DESC LIMIT 3"""
)
q(
    "orders column probe",
    """SELECT order_id, customer_id, address_id, created_at
       FROM orders ORDER BY order_id DESC LIMIT 1"""
)

# Probe which payment/status columns exist on orders
order_cols = {c["name"] for c in d["tables"]["orders"]["columns"]}
interesting = sorted(
    c for c in order_cols
    if any(k in c.lower() for k in (
        "status", "payment", "total", "amount", "customer", "address",
        "source", "channel", "mobile", "deliver", "driver", "cancel",
        "created", "deleted", "schedule", "slot", "type", "grand", "vat"
    ))
)
lines.append("")
lines.append("ORDERS voice-interesting columns:")
for c in interesting:
    lines.append(f"  {c}")

deliv_cols = {c["name"] for c in d["tables"]["deliveries"]["columns"]}
interesting_d = sorted(
    c for c in deliv_cols
    if any(k in c.lower() for k in (
        "status", "order", "driver", "deliver", "customer", "address",
        "created", "deleted", "trip", "eta", "time", "date", "lat", "long"
    ))
)
lines.append("")
lines.append("DELIVERIES voice-interesting columns:")
for c in interesting_d:
    lines.append(f"  {c}")

cust_cols = {c["name"] for c in d["tables"]["customers"]["columns"]}
interesting_c = sorted(
    c for c in cust_cols
    if any(k in c.lower() for k in (
        "mobile", "name", "email", "status", "customer", "created", "deleted",
        "phone", "lang", "group", "type", "block"
    ))
)
lines.append("")
lines.append("CUSTOMERS voice-interesting columns:")
for c in interesting_c:
    lines.append(f"  {c}")

addr_cols = {c["name"] for c in d["tables"]["addresses"]["columns"]}
interesting_a = sorted(
    c for c in addr_cols
    if any(k in c.lower() for k in (
        "address", "customer", "map", "lat", "long", "type", "created",
        "updated", "deleted", "city", "district", "street", "building", "default"
    ))
)
lines.append("")
lines.append("ADDRESSES voice-interesting columns:")
for c in interesting_a:
    lines.append(f"  {c}")

# Recent order with status join if possible
status_cols = [c["name"] for c in d["tables"]["order_statuses"]["columns"]]
lines.append("")
lines.append("order_statuses columns: " + ", ".join(status_cols))

oi_cols = [c["name"] for c in d["tables"]["order_items"]["columns"]]
lines.append("order_items columns: " + ", ".join(oi_cols))

# Sample join pattern that tools already use
q(
    "recent order_items join products",
    """SELECT o.order_id, o.customer_id, o.created_at, oi.product_id, oi.quantity, p.product_name
       FROM orders o
       JOIN order_items oi ON oi.order_id = o.order_id
       JOIN products p ON p.product_id = oi.product_id
       ORDER BY o.order_id DESC LIMIT 5"""
)

# deliveries sample
deliv_name_cols = [c["name"] for c in d["tables"]["deliveries"]["columns"][:20]]
q("deliveries recent", f"SELECT {', '.join('`'+c+'`' for c in deliv_name_cols)} FROM deliveries ORDER BY 1 DESC LIMIT 3")

conn.close()
text = "\n".join(lines)
open(out_path, "w", encoding="utf-8").write(text)
print("WROTE", out_path, "lines", len(lines))
