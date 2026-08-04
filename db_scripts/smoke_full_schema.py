"""Smoke: full schema bible allowlist — browse, top JOIN, track, temp, rejects."""
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db.tools import query_catalog
from db.sql_sandbox import validate_catalog_sql, load_relevant_ddl, ALLOWED_TABLES
from db.db import get_connection
from core.prompts import ORDER_SYSTEM_PROMPT

# Schema load
schema = load_relevant_ddl()
assert "TABLE products" in schema or "### TABLE products" in schema, "bible missing products"
assert "tania_temp_orders" in schema
assert "Phases 1–5 are ENABLED" in schema or "ENABLED" in schema
assert len(ALLOWED_TABLES) >= 15
print("SCHEMA_LOAD_OK", len(schema), "chars", len(ALLOWED_TABLES), "tables")

assert "Schema SQL intelligence" in ORDER_SYSTEM_PROMPT
assert "Relevant DB Schema" in ORDER_SYSTEM_PROMPT or "query_catalog" in ORDER_SYSTEM_PROMPT
print("PROMPT_OK")

# Reject mutating / bad tables
cleaned, err = validate_catalog_sql("DELETE FROM products")
assert cleaned is None and err, err
print("REJECT_DELETE", err)

cleaned, err = validate_catalog_sql("SELECT * FROM vehicles LIMIT 5")
assert cleaned is None and "table_not_allowed" in (err or ""), err
print("REJECT_VEHICLES", err)

cleaned, err = validate_catalog_sql(
    "SELECT order_id, grand_total FROM orders ORDER BY created_at DESC LIMIT 5"
)
assert cleaned is None and "missing_orders_filter" in (err or ""), err
print("REJECT_UNFILTERED_ORDERS", err)

# Browse products
sql_browse = (
    "SELECT product_id, product_name, unit, price_vat FROM products "
    "WHERE status = 1 AND show_in_oms = 1 AND deleted_at IS NULL AND price_vat > 0 "
    "ORDER BY product_sort LIMIT 5"
)
r = query_catalog(sql_browse)
assert not r.get("error"), r
assert r.get("count", 0) >= 1, r
print("BROWSE_OK", r["count"], r["rows"][0].get("name_en") or r["rows"][0].get("product_id"))

# Top / bestseller JOIN
sql_top = (
    "SELECT p.product_id, p.product_name, p.unit, p.price_vat, COUNT(*) AS order_count "
    "FROM order_items oi "
    "JOIN products p ON p.product_id = oi.product_id "
    "WHERE p.status = 1 AND p.show_in_oms = 1 AND p.deleted_at IS NULL AND p.price_vat > 0 "
    "AND oi.deleted_at IS NULL "
    "GROUP BY p.product_id, p.product_name, p.unit, p.price_vat "
    "ORDER BY order_count DESC LIMIT 5"
)
r2 = query_catalog(sql_top)
assert not r2.get("error"), r2
assert r2.get("count", 0) >= 1, r2
print("TOP_JOIN_OK", r2["count"], r2["rows"][0].get("name_en"), r2["rows"][0].get("order_count"))

# Track-style (pick a real customer_id from a recent order)
conn = get_connection()
cur = conn.cursor()
cur.execute(
    "SELECT customer_id FROM orders WHERE deleted_at IS NULL AND customer_id IS NOT NULL "
    "ORDER BY order_id DESC LIMIT 1"
)
row = cur.fetchone()
cur.close()
conn.close()
assert row and row[0], "no customer_id for track smoke"
cid = int(row[0])

sql_track = (
    f"SELECT o.order_id, o.grand_total, o.created_at, os.order_status_title, os.`key` AS status_key "
    f"FROM orders o "
    f"JOIN order_statuses os ON os.order_status_id = o.order_status_id "
    f"WHERE o.customer_id = {cid} AND o.deleted_at IS NULL "
    f"ORDER BY o.created_at DESC LIMIT 5"
)
r3 = query_catalog(sql_track)
assert not r3.get("error"), r3
print("TRACK_OK", r3.get("count"), "customer_id", cid)

# Temp orders (may be empty)
sql_temp = (
    "SELECT temp_order_id, status, total_amount, payment_method, created_at "
    "FROM tania_temp_orders WHERE temp_customer_id = 1 ORDER BY created_at DESC LIMIT 5"
)
r4 = query_catalog(sql_temp)
assert not r4.get("error"), r4
print("TEMP_OK", r4.get("count"))

print("SMOKE_FULL_SCHEMA_OK")
