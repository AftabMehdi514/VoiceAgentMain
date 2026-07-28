"""
product_search.py
=================
Search tool for finding products by keyword from the database.
Called from tools.py via execute_tool("search_products", {...}).

Handles:
- Exact / near-exact matches (e.g. "200ml", "gallon", "330")
- Bilingual product names stored as JSON {"en":"...", "ar":"..."}
- Returns top results ranked by real order frequency
- Max 5 results to keep responses concise for voice

Import this module from tools.py — do NOT run standalone.
"""
import re
import difflib
import json as _json
from db.db import get_connection

def _parse_name(raw):
    """Parse bilingual JSON product name into (name_en, name_ar)."""
    if not raw:
        return "", ""
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            d = _json.loads(raw)
            return d.get("en", "").strip(), d.get("ar", "").strip()
        except Exception:
            pass
    return str(raw).strip(), str(raw).strip()

def extract_numbers(text):
    """Extract numbers including decimals as tokens."""
    if not text:
        return []
    # matches digits and decimals like 3.8, 200
    return re.findall(r'\d+(?:\.\d+)?', text)

def search_products(query, limit=5):
    """
    Search products by keyword using two-stage matching:
    1. Numeric hard filter: If query has numbers, product must match exactly.
    2. Fuzzy scoring: Score survivors using string similarity.
    Results are ranked by fuzzy score and then real order frequency.
    """
    if not query or not str(query).strip():
        return {"error": "query_empty"}

    limit = min(int(limit), 10)
    query_str = str(query).strip().lower()

    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}

    try:
        cursor = conn.cursor(dictionary=True)

        # Fetch all active products
        cursor.execute("""
            SELECT
                p.product_id,
                p.product_name,
                p.price_vat,
                p.unit,
                COALESCE(freq.order_count, 0) AS order_count
            FROM products p
            LEFT JOIN (
                SELECT product_id, COUNT(*) AS order_count
                FROM order_items
                GROUP BY product_id
            ) freq ON freq.product_id = p.product_id
            WHERE p.status = 1
              AND p.show_in_oms = 1
        """)

        rows = cursor.fetchall()

        if not rows:
            return []

        candidates = []
        for r in rows:
            name_en, name_ar = _parse_name(r["product_name"])
            unit_en, unit_ar = _parse_name(r.get("unit", ""))
            candidates.append({
                "product_id": r["product_id"],
                "name_en": name_en,
                "name_ar": name_ar,
                "unit_en": unit_en,
                "unit_ar": unit_ar,
                "price_vat": float(r["price_vat"]) if r["price_vat"] else 0.0,
                "order_count": r["order_count"],
            })

        # Stage 1: Numeric hard filter
        query_numbers = set(extract_numbers(query_str))
        stage1_survivors = []
        
        if query_numbers:
            for c in candidates:
                prod_text = f"{c['name_en']} {c['name_ar']} {c['unit_en']} {c['unit_ar']}".lower()
                prod_numbers = set(extract_numbers(prod_text))
                # Product must contain at least one of the queried numbers exactly
                if query_numbers.intersection(prod_numbers):
                    stage1_survivors.append(c)
        else:
            stage1_survivors = candidates

        # Stage 2: Fuzzy scoring
        for c in stage1_survivors:
            # Score against English and Arabic separately and take the max
            score_en = difflib.SequenceMatcher(None, query_str, c["name_en"].lower()).ratio()
            score_ar = difflib.SequenceMatcher(None, query_str, c["name_ar"].lower()).ratio()
            c["fuzzy_score"] = max(score_en, score_ar)

        # Sort by fuzzy score descending, then by order count descending
        stage1_survivors.sort(key=lambda x: (x["fuzzy_score"], x["order_count"]), reverse=True)

        return stage1_survivors[:limit]

    except Exception as e:
        print(f"[search_products] Error: {e}")
        return {"error": str(e)}
    finally:
        if "cursor" in dir():
            cursor.close()
        conn.close()
