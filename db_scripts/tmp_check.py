import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
﻿import sys, mysql.connector, json
sys.stdout.reconfigure(encoding="utf-8")
from config import DBPassword
conn = mysql.connector.connect(host="localhost", user="root", password=DBPassword, database="tania_db")
c = conn.cursor(dictionary=True)

print("=== TOP 6 MOST ORDERED PRODUCTS (production orders) ===")
c.execute("""
    SELECT oi.product_id, p.product_name, COUNT(*) as order_count, SUM(oi.quantity) as total_qty
    FROM order_items oi
    JOIN products p ON p.product_id = oi.product_id
    WHERE p.status = 1 AND p.show_in_oms = 1
    GROUP BY oi.product_id, p.product_name
    ORDER BY order_count DESC
    LIMIT 6
""")
for r in c.fetchall():
    print(json.dumps({k: str(v) if v is not None else None for k,v in r.items()}, ensure_ascii=False))

print("\n=== CUSTOMER 1 PREVIOUS ORDERS (last 3) ===")
c.execute("""
    SELECT o.order_id, o.created_at, oi.product_id, p.product_name, oi.quantity, oi.unit_price_vat
    FROM orders o
    JOIN order_items oi ON oi.order_id = o.order_id
    JOIN products p ON p.product_id = oi.product_id
    WHERE o.customer_id = 1
    ORDER BY o.created_at DESC
    LIMIT 6
""")
for r in c.fetchall():
    print(json.dumps({k: str(v) if v is not None else None for k,v in r.items()}, ensure_ascii=False))
conn.close()
