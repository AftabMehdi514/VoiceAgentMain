"""Smoke test for query_catalog sandbox (query-only product path)."""
from db.tools import query_catalog, execute_tool
from db.sql_sandbox import validate_catalog_sql

cleaned, err = validate_catalog_sql("DELETE FROM products")
assert cleaned is None and err, err
print("REJECT_DELETE", err)

cleaned, err = validate_catalog_sql("SELECT product_id FROM products LIMIT 3")
assert cleaned is None and "missing_availability" in (err or ""), err
print("REJECT_NOFILTER", err)

# Open browse shape
sql_browse = (
    "SELECT product_id, product_name, unit, price_vat FROM products "
    "WHERE status = 1 AND show_in_oms = 1 AND deleted_at IS NULL "
    "AND price_vat > 0 ORDER BY product_sort LIMIT 5"
)
rb = query_catalog(sql_browse)
assert not rb.get("error"), rb
assert rb.get("count", 0) >= 1, rb
print("BROWSE", rb["count"], rb["rows"][0].get("product_id"), rb["rows"][0].get("name_en"))

sql = (
    "SELECT product_id, product_name, unit, price_vat FROM products "
    "WHERE status = 1 AND show_in_oms = 1 AND deleted_at IS NULL "
    "AND price_vat > 0 AND product_name LIKE '%200%' ORDER BY product_sort LIMIT 5"
)
r = query_catalog(sql)
assert not r.get("error"), r
assert r.get("count", 0) >= 1, r
row = r["rows"][0]
assert "product_id" in row and ("name_en" in row or "name" in row)
print("SEARCH", r["count"], row.get("product_id"), row.get("name_en"), row.get("price_vat"))

r2 = execute_tool("query_catalog", {"sql": sql})
assert r2.get("count", 0) >= 1, r2
print("TOOL_OK", r2["count"])

# Legacy tools not on LLM registry
assert execute_tool("get_top_products", {}).get("error", "").startswith("unknown_tool")
print("NO_TOP_TOOL_OK")

sql_g = (
    "SELECT product_id, product_name, unit, price_vat FROM products "
    "WHERE status=1 AND show_in_oms=1 AND deleted_at IS NULL AND price_vat > 0 "
    "AND (product_name LIKE '%gallon%' OR product_name LIKE '%Gallon%' OR unit LIKE '%Gallon%') "
    "LIMIT 5"
)
r3 = query_catalog(sql_g)
assert not r3.get("error"), r3
assert r3.get("count", 0) >= 1, r3
print("GALLON", r3["count"], r3["rows"][0].get("name_en"), r3["rows"][0].get("product_id"))

sql_cheap = (
    "SELECT product_id, product_name, unit, price_vat FROM products "
    "WHERE status=1 AND show_in_oms=1 AND deleted_at IS NULL AND price_vat > 0 "
    "ORDER BY price_vat ASC LIMIT 5"
)
r4 = query_catalog(sql_cheap)
assert not r4.get("error"), r4
assert r4.get("count", 0) >= 1, r4
print("CHEAPEST", r4["rows"][0].get("price_vat"), r4["rows"][0].get("name_en"))

chosen = {
    "product_id": row["product_id"],
    "quantity": 2,
    "name": row.get("name_en") or row.get("name"),
}
print("ITEMS_SAMPLE", chosen)
print("SMOKE_OK")
