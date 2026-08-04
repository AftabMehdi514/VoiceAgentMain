"""Explore tania_db schema for voice-agent relevant tables."""
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from db.db import get_connection

CANDIDATES = [
    # catalog
    "products", "categories", "product_categories", "products_in_category",
    "similar_products", "variants", "channel_product_pricing",
    "customer_product_pricing", "group_product_pricing", "product_stocks",
    "product_gallery",
    # customer / address
    "customers", "addresses", "address_types", "customer_groups",
    "potential_customers", "customer_delivery_slots", "delivery_slots",
    # order lifecycle
    "orders", "order_items", "order_statuses", "order_notify", "payments",
    "invoices", "cancel_reasons", "refund_reasons", "return_reasons",
    "rejection_reasons", "sources", "channels",
    # delivery / tracking
    "deliveries", "sub_deliveries", "delivery_trips", "dynamic_deliveries",
    "split_order_deliveries", "tracking_data", "tracking_data_1",
    "tracker_data", "trip_statuses", "drivers", "vehicles", "routes",
    # voice agent temp
    "tania_temp_customers", "tania_temp_addresses", "tania_temp_orders",
    # loyalty / coupons (future)
    "coupons", "promocodes", "promotions", "loyalty_configurations",
    "wallet_transactions", "water_drops",
    # support tickets (future)
    "tickets", "ticket_categories", "ticket_products",
]

SAMPLE_TABLES = {
    "products": [
        "product_id", "product_name", "unit", "price_vat", "status",
        "show_in_oms", "deleted_at", "bottle_quantity", "product_sort", "sku",
        "category_id",
    ],
    "categories": None,
    "customers": [
        "customer_id", "mobile", "name", "email", "status", "created_at",
    ],
    "addresses": [
        "address_id", "customer_id", "address", "map_info", "latitude",
        "longitude", "updated_at", "deleted_at",
    ],
    "orders": None,  # pick key cols after describe
    "order_items": None,
    "order_statuses": None,
    "deliveries": None,
    "tania_temp_customers": None,
    "tania_temp_addresses": None,
    "tania_temp_orders": None,
}


def main():
    conn = get_connection()
    if not conn:
        raise SystemExit("no db connection")
    cur = conn.cursor()
    cur.execute("SHOW TABLES")
    existing = {r[0] for r in cur.fetchall()}
    seen = set()
    cands = []
    for t in CANDIDATES:
        if t in existing and t not in seen:
            cands.append(t)
            seen.add(t)

    print("CANDIDATES", len(cands), "of", len(existing), "total tables")
    out = {"database": "tania_db", "total_tables": len(existing), "tables": {}}

    for t in cands:
        info = {"columns": [], "indexes": [], "row_count": None, "create": None, "sample": []}
        cur.execute(f"DESCRIBE `{t}`")
        for r in cur.fetchall():
            info["columns"].append({
                "name": r[0],
                "type": r[1],
                "null": r[2],
                "key": r[3],
                "default": None if r[4] is None else str(r[4]),
                "extra": r[5] or "",
            })
        try:
            cur.execute(f"SHOW INDEX FROM `{t}`")
            idxs = {}
            for r in cur.fetchall():
                kn, col = r[2], r[4]
                idxs.setdefault(kn, {"unique": r[1] == 0, "cols": []})
                idxs[kn]["cols"].append(col)
            info["indexes"] = [{"name": k, **v} for k, v in idxs.items()]
        except Exception as e:
            info["index_error"] = str(e)
        try:
            cur.execute(f"SELECT COUNT(*) FROM `{t}`")
            info["row_count"] = cur.fetchone()[0]
        except Exception as e:
            info["count_error"] = str(e)
        try:
            cur.execute(f"SHOW CREATE TABLE `{t}`")
            info["create"] = cur.fetchone()[1]
        except Exception as e:
            info["create_error"] = str(e)

        # samples
        try:
            cols = SAMPLE_TABLES.get(t)
            if cols is None:
                cols = [c["name"] for c in info["columns"][:14]]
            else:
                cols = [c for c in cols if any(x["name"] == c for x in info["columns"])]
            if cols:
                colsql = ", ".join(f"`{c}`" for c in cols)
                cur.execute(f"SELECT {colsql} FROM `{t}` LIMIT 3")
                rows = cur.fetchall()
                for row in rows:
                    item = {}
                    for i, c in enumerate(cols):
                        v = row[i]
                        s = None if v is None else str(v)
                        if s and len(s) > 180:
                            s = s[:180] + "…"
                        item[c] = s
                    info["sample"].append(item)
        except Exception as e:
            info["sample_error"] = str(e)

        out["tables"][t] = info
        print(f"{t}: {info['row_count']} rows, {len(info['columns'])} cols")

    # Also dump FK-ish relationships from information_schema
    cur.execute("""
        SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE()
          AND REFERENCED_TABLE_NAME IS NOT NULL
          AND TABLE_NAME IN (%s)
        ORDER BY TABLE_NAME, COLUMN_NAME
    """ % (",".join(["%s"] * len(cands))), tuple(cands))
    fks = []
    for r in cur.fetchall():
        fks.append({
            "table": r[0], "column": r[1],
            "ref_table": r[2], "ref_column": r[3],
        })
    out["foreign_keys"] = fks
    print("FKs among candidates:", len(fks))

    path = os.path.join(os.path.dirname(__file__), "_schema_dump.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("WROTE", path)
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
