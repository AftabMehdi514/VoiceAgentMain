import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
﻿import sys, mysql.connector, json
sys.stdout.reconfigure(encoding="utf-8")
from config import DBPassword
conn = mysql.connector.connect(host="localhost", user="root", password=DBPassword, database="tania_db")
c = conn.cursor(dictionary=True)

print("=== SAMPLE PRODUCTS ===")
c.execute("""
    SELECT product_id, product_name, price_vat, unit
    FROM products
    WHERE status = 1 AND show_in_oms = 1
    ORDER BY product_sort_cc
    LIMIT 20
""")
products = []
for r in c.fetchall():
    products.append({k: str(v) if v is not None else None for k,v in r.items()})
    print(json.dumps(products[-1], ensure_ascii=False))

with open("db_cache_products.md", "w", encoding="utf-8") as f:
    f.write("# DB Product Cache\n\n")
    f.write("```json\n")
    f.write(json.dumps(products, indent=2, ensure_ascii=False))
    f.write("\n```\n")

conn.close()
