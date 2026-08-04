"""
sql_sandbox.py
==============
Safe execution of LLM-authored SELECTs against the voice-agent schema bible.
Enforces allowlist + table-aware filters — does NOT hardcode business queries.
"""
from __future__ import annotations

import os
import re
import json as _json
from db.db import get_connection

ALLOWED_TABLES = {
    "products",
    "categories",
    "products_in_category",
    "similar_products",
    "order_items",
    "orders",
    "order_statuses",
    "customers",
    "addresses",
    "address_types",
    "deliveries",
    "delivery_trips",
    "trip_statuses",
    "payments",
    "sources",
    "cancel_reasons",
    "delivery_slots",
    "tania_temp_customers",
    "tania_temp_addresses",
    "tania_temp_orders",
}

MAX_LIMIT = 8
MAX_ROWS = 8

_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|REPLACE|TRUNCATE|GRANT|REVOKE|"
    r"CALL|EXEC|EXECUTE|INTO\s+OUTFILE|INTO\s+DUMPFILE|LOAD\s+DATA|SET\s+|SLEEP\s*\(|"
    r"BENCHMARK\s*\(|INFORMATION_SCHEMA|PERFORMANCE_SCHEMA|mysql\.|sys\.)\b",
    re.IGNORECASE,
)

_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)\b", re.IGNORECASE)
_FROM_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+`?([a-zA-Z_][a-zA-Z0-9_]*)`?",
    re.IGNORECASE,
)

# Predicate helpers (alias-friendly: mobile, c.mobile, customers.mobile)
def _has_pred(sql_low: str, *names: str) -> bool:
    for name in names:
        if re.search(rf"(?:^|[^a-z0-9_])(?:[a-z_][a-z0-9_]*\.)?{name}(?:\s*=|\s+in\s*\(|\s+like\b)", sql_low):
            return True
        # also allow IS NULL checks for deleted_at
        if name == "deleted_at" and "deleted_at" in sql_low:
            return True
    return False


def _parse_bilingual(raw):
    if not raw:
        return "", ""
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            data = _json.loads(raw)
            return (data.get("en") or "").strip(), (data.get("ar") or "").strip()
        except Exception:
            pass
    s = str(raw).strip()
    return s, s


def _normalize_row(row: dict) -> dict:
    out = dict(row)
    if "product_name" in out:
        en, ar = _parse_bilingual(out.get("product_name"))
        out["name_en"] = en
        out["name_ar"] = ar
        out["name"] = en or ar
    if "unit" in out:
        uen, uar = _parse_bilingual(out.get("unit"))
        out["unit_en"] = uen
        out["unit_ar"] = uar
        out["unit_label"] = uen or uar
    for k, v in list(out.items()):
        if hasattr(v, "as_tuple"):  # Decimal
            out[k] = float(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
    return out


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    sql = re.sub(r"--.*?$", " ", sql, flags=re.MULTILINE)
    return sql.strip()


def _table_aware_filters(tables: set[str], sql_low: str) -> str | None:
    """Return error string if mandatory filters missing."""
    # products → availability (including JOINs that expose sellable catalog)
    if "products" in tables:
        if "show_in_oms" not in sql_low:
            return (
                "missing_product_availability:"
                "add products filters status = 1 AND show_in_oms = 1 AND deleted_at IS NULL"
            )
        if "status" not in sql_low:
            return (
                "missing_product_availability:"
                "add products filters status = 1 AND show_in_oms = 1 AND deleted_at IS NULL"
            )
        if "deleted_at" not in sql_low:
            return (
                "missing_product_availability:"
                "add AND deleted_at IS NULL on products (and order_items if used)"
            )

    if "customers" in tables:
        if not (_has_pred(sql_low, "mobile") or _has_pred(sql_low, "customer_id")):
            return "missing_customers_filter:require WHERE mobile = ... or customer_id = ..."

    if "addresses" in tables:
        if not (_has_pred(sql_low, "customer_id") or _has_pred(sql_low, "address_id")):
            return "missing_addresses_filter:require WHERE customer_id = ... or address_id = ..."

    if "orders" in tables:
        if not (
            _has_pred(sql_low, "customer_id")
            or _has_pred(sql_low, "order_id")
            or _has_pred(sql_low, "order_number")
        ):
            return (
                "missing_orders_filter:"
                "require WHERE customer_id = ... or order_id = ... or order_number = ..."
            )

    if "deliveries" in tables:
        if not (_has_pred(sql_low, "order_id") or _has_pred(sql_low, "delivery_id")):
            return "missing_deliveries_filter:require WHERE order_id = ... or delivery_id = ..."

    temp_tables = tables & {
        "tania_temp_customers",
        "tania_temp_addresses",
        "tania_temp_orders",
    }
    if temp_tables:
        if not (
            _has_pred(sql_low, "temp_customer_id")
            or _has_pred(sql_low, "temp_order_id")
            or _has_pred(sql_low, "temp_address_id")
            or _has_pred(sql_low, "mobile")
        ):
            return (
                "missing_temp_filter:"
                "require temp_customer_id / temp_order_id / temp_address_id / mobile"
            )

    # order_items alone for popularity (top products) — OK without customer_id,
    # but if not joining products, still require deleted_at when column is used in bible
    if "order_items" in tables and "products" not in tables and "orders" not in tables:
        if "deleted_at" not in sql_low:
            return "missing_order_items_filter:add oi.deleted_at IS NULL (or order_items.deleted_at IS NULL)"

    return None


def validate_catalog_sql(sql: str) -> tuple[str | None, str | None]:
    """
    Returns (cleaned_sql, error_message).
    error_message is None when valid.
    """
    if not sql or not str(sql).strip():
        return None, "empty_sql"

    cleaned = _strip_sql_comments(str(sql)).strip().rstrip(";")
    if not cleaned:
        return None, "empty_sql"

    if ";" in cleaned:
        return None, "multiple_statements_not_allowed"

    if not re.match(r"^\s*SELECT\b", cleaned, re.IGNORECASE):
        return None, "only_select_allowed"

    if _FORBIDDEN.search(cleaned):
        return None, "forbidden_keyword_or_schema"

    tables = {t.lower() for t in _FROM_TABLE_RE.findall(cleaned)}
    if not tables:
        return None, "no_table_found"
    bad = tables - ALLOWED_TABLES
    if bad:
        return None, f"table_not_allowed:{','.join(sorted(bad))}"

    low = cleaned.lower()
    filter_err = _table_aware_filters(tables, low)
    if filter_err:
        return None, filter_err

    m = _LIMIT_RE.search(cleaned)
    if m:
        lim = int(m.group(1))
        if lim > MAX_LIMIT:
            cleaned = _LIMIT_RE.sub(f"LIMIT {MAX_LIMIT}", cleaned, count=1)
    else:
        cleaned = f"{cleaned} LIMIT {MAX_LIMIT}"

    return cleaned, None


def execute_catalog_sql(sql: str) -> dict:
    """
    Validate and run a catalog/schema SELECT. Returns {rows, sql, count} or {error}.
    """
    cleaned, err = validate_catalog_sql(sql)
    if err:
        return {
            "error": err,
            "hint": (
                "SELECT-only on allowlisted Relevant_DB_Schema tables. "
                "Match filters to tables used (products availability; "
                "customers/orders/addresses/deliveries/temp keyed WHERE). LIMIT <= 8."
            ),
        }

    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(cleaned)
        raw_rows = cursor.fetchmany(MAX_ROWS)
        rows = [_normalize_row(r) for r in raw_rows]
        return {
            "sql": cleaned,
            "count": len(rows),
            "rows": rows,
        }
    except Exception as e:
        return {"error": f"sql_execution_failed:{e}", "sql": cleaned}
    finally:
        try:
            cursor.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def load_relevant_ddl(path: str | None = None) -> str:
    """Load the voice-agent schema bible (Relevant_DB_Schema.txt)."""
    if path is None:
        path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "Relevant_DB_Schema.txt")
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"(Relevant_DB_Schema.txt unavailable: {e})"
