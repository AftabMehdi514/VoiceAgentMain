import json as _json
from db.db import get_connection
from core.telemetry import Span

# ── Module-level product cache (populated ONCE at startup) ────────────────────
_TOP_PRODUCTS_CACHE = None

def _parse_bilingual(raw, key_en="en", key_ar="ar"):
    """Safely parse a bilingual JSON string like {"en":"...", "ar":"..."}."""
    if not raw:
        return "", ""
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            data = _json.loads(raw)
            return data.get(key_en, ""), data.get(key_ar, "")
        except Exception:
            pass
    return str(raw), str(raw)


def get_top_products():
    """Fetch top 6 most-ordered products. Cached at module level — only one DB hit ever."""
    global _TOP_PRODUCTS_CACHE
    if _TOP_PRODUCTS_CACHE is not None:
        return _TOP_PRODUCTS_CACHE

    conn = get_connection()
    if not conn:
        _TOP_PRODUCTS_CACHE = []
        return _TOP_PRODUCTS_CACHE

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT p.product_id, p.product_name, p.price_vat, p.unit
            FROM (
                SELECT oi.product_id, COUNT(*) AS cnt
                FROM order_items oi
                GROUP BY oi.product_id
                ORDER BY cnt DESC
                LIMIT 20
            ) ranked
            JOIN products p ON p.product_id = ranked.product_id
            WHERE p.status = 1 AND p.show_in_oms = 1
            ORDER BY ranked.cnt DESC
            LIMIT 6
        """)
        rows = cursor.fetchall()
        result = []
        for r in rows:
            name_en, name_ar = _parse_bilingual(r["product_name"])
            unit_en, unit_ar = _parse_bilingual(r["unit"])
            result.append({
                "product_id": r["product_id"],
                "name_en": name_en.strip(),
                "name_ar": name_ar.strip(),
                "unit_en": unit_en,
                "unit_ar": unit_ar,
                "price_vat": float(r["price_vat"]) if r["price_vat"] else 0.0,
            })
        _TOP_PRODUCTS_CACHE = result
        return result
    except Exception as e:
        print(f"[get_top_products] Error: {e}")
        _TOP_PRODUCTS_CACHE = []
        return []
    finally:
        if "cursor" in dir():
            cursor.close()
        conn.close()


def get_products():
    """Return the cached top products (same as get_top_products — tool alias for the LLM)."""
    return get_top_products()

from db.product_search import search_products

def check_active_products(product_ids):
    """Takes a list of product_ids and returns a set of valid active product_ids in the DB."""
    if not product_ids:
        return set()
    conn = get_connection()
    if not conn:
        return set()
    try:
        cursor = conn.cursor(dictionary=True)
        format_strings = ','.join(['%s'] * len(product_ids))
        query = f"SELECT product_id FROM products WHERE status = 1 AND show_in_oms = 1 AND product_id IN ({format_strings})"
        cursor.execute(query, tuple(product_ids))
        return {r["product_id"] for r in cursor.fetchall()}
    except Exception as e:
        print(f"[check_active_products] Error: {e}")
        return set()
    finally:
        if "cursor" in dir():
            cursor.close()
        conn.close()

def build_basket_fingerprint(items):
    """Build a stable fingerprint for a given basket to detect changes."""
    if not items:
        return ""
    sorted_items = sorted(items, key=lambda x: str(x.get("product_id")))
    parts = []
    for item in sorted_items:
        parts.append(f"{item.get('product_id')}:{item.get('quantity', 1)}")
    return "|".join(parts)

def get_price(product_id, quantity):
    qty = int(quantity) if quantity else 1
    products = get_top_products()
    unit_price = 5.0
    for p in products:
        if str(p.get("product_id")) == str(product_id):
            unit_price = p.get("price_vat", 5.0)
            break
    return {
        "product_id": product_id,
        "quantity": qty,
        "unit_price": unit_price,
        "total_price": round(unit_price * qty, 2),
        "currency": "SAR",
    }


def calculate_order_total(items):
    total = 0.0
    for item in items:
        info = get_price(item.get("product_id"), item.get("quantity", 1))
        total += info["total_price"]
    return {"total_amount": round(total, 2), "currency": "SAR"}


def lookup_customer(mobile=None):
    if not mobile:
        return {"found": False}
    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        # Try production customers first (read-only)
        try:
            cursor.execute(
                "SELECT customer_id, name, email FROM customers WHERE mobile = %s LIMIT 1",
                (mobile,)
            )
            row = cursor.fetchone()
            if row:
                return {"found": True, "customer_id": row["customer_id"], "name": row.get("name"), "email": row.get("email")}
        except Exception:
            pass
        # Fallback to temp table
        cursor.execute(
            "SELECT temp_customer_id, name, email FROM tania_temp_customers WHERE mobile = %s LIMIT 1",
            (mobile,)
        )
        row = cursor.fetchone()
        if row:
            return {"found": True, "customer_id": row["temp_customer_id"], "name": row.get("name"), "email": row.get("email"), "is_temp": True}
        return {"found": False}
    finally:
        cursor.close()
        conn.close()


def get_customer_orders(customer_id=None):
    """Return last 3 orders for personalisation ('Last time you ordered...')."""
    if not customer_id:
        return []
    conn = get_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT o.order_id, o.created_at, oi.product_id, p.product_name, oi.quantity
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.order_id
            JOIN products p ON p.product_id = oi.product_id
            WHERE o.customer_id = %s
            ORDER BY o.created_at DESC
            LIMIT 6
        """, (customer_id,))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            name_en, name_ar = _parse_bilingual(r["product_name"])
            result.append({
                "order_id": r["order_id"],
                "date": str(r["created_at"]).split(" ")[0],
                "product_id": r["product_id"],
                "name_en": name_en.strip(),
                "name_ar": name_ar.strip(),
                "quantity": r["quantity"],
            })
        return result
    except Exception as e:
        print(f"[get_customer_orders] Error: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def get_latest_address(customer_id=None):
    if not customer_id:
        return {"found": False}
    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}
    try:
        cursor = conn.cursor(dictionary=True)
        # Production addresses table — PK is address_id
        try:
            cursor.execute(
                "SELECT address_id, address, map_info, latitude, longitude FROM addresses WHERE customer_id = %s ORDER BY updated_at DESC LIMIT 1",
                (customer_id,)
            )
            row = cursor.fetchone()
            if row:
                map_info = row.get("map_info")
                if not map_info and row.get("latitude"):
                    map_info = _json.dumps({"latitude": str(row["latitude"]), "longitude": str(row["longitude"])})
                return {
                    "found": True,
                    "address": row["address"],
                    "address_id": row["address_id"],
                    "map_info": map_info,
                }
        except Exception:
            pass
        # Temp addresses
        cursor.execute(
            "SELECT temp_address_id, address, map_info FROM tania_temp_addresses WHERE temp_customer_id = %s ORDER BY created_at DESC LIMIT 1",
            (customer_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "found": True,
                "address": row["address"],
                "address_id": row["temp_address_id"],
                "map_info": row.get("map_info"),
            }
        return {"found": False}
    finally:
        cursor.close()
        conn.close()


def save_customer_and_address(mobile, address_text, name=None):
    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}
    try:
        cursor = conn.cursor()
        # Upsert customer
        cursor.execute("SELECT temp_customer_id FROM tania_temp_customers WHERE mobile = %s", (mobile,))
        row = cursor.fetchone()
        if row:
            temp_customer_id = row[0]
        else:
            cursor.execute(
                "INSERT INTO tania_temp_customers (mobile, name) VALUES (%s, %s)",
                (mobile, name or "")
            )
            temp_customer_id = cursor.lastrowid
        # Insert address
        default_map_info = _json.dumps({"latitude": "0.000000", "longitude": "0.000000"})
        cursor.execute(
            "INSERT INTO tania_temp_addresses (temp_customer_id, address, map_info) VALUES (%s, %s, %s)",
            (temp_customer_id, address_text, default_map_info)
        )
        temp_address_id = cursor.lastrowid
        conn.commit()
        return {
            "success": True,
            "temp_customer_id": temp_customer_id,
            "temp_address_id": temp_address_id,
            "map_info": default_map_info,
        }
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()


def create_order(customer_id, address_id, items, total_amount, payment_method):
    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}
    try:
        cursor = conn.cursor()
        items_json = _json.dumps(items)
        cursor.execute(
            "INSERT INTO tania_temp_orders (temp_customer_id, temp_address_id, items, total_amount, payment_method, status) VALUES (%s, %s, %s, %s, %s, 'confirmed')",
            (customer_id, address_id, items_json, total_amount, payment_method)
        )
        temp_order_id = cursor.lastrowid
        conn.commit()
        return {"success": True, "temp_order_id": temp_order_id}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()


def save_rating(temp_order_id, rating):
    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}
    try:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE tania_temp_orders SET rating = %s WHERE temp_order_id = %s",
            (rating, temp_order_id)
        )
        conn.commit()
        return {"success": True}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()


TOOL_REGISTRY = {
    "lookup_customer": lookup_customer,
    "get_customer_orders": get_customer_orders,
    "get_latest_address": get_latest_address,
    "save_customer_and_address": save_customer_and_address,
    "get_products": get_products,
    "get_top_products": get_top_products,
    "search_products": search_products,
    "get_price": get_price,
    "calculate_order_total": calculate_order_total,
    "create_order": create_order,
    "save_rating": save_rating,
}

def execute_tool(name, arguments):
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return {"error": f"unknown_tool:{name}"}
    
    with Span(f"db_tool_{name}", metadata={"arguments": arguments}) as span:
        try:
            result = fn(**(arguments or {}))
            span.exit_metadata = {"result": result}
            return result
        except TypeError as e:
            error = f"bad_arguments:{e}"
            span.exit_metadata = {"error": error}
            return {"error": error}
        except Exception as e:
            error = f"tool_error:{e}"
            span.exit_metadata = {"error": error}
            return {"error": error}
