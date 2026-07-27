import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
﻿import sys, mysql.connector, json
sys.stdout.reconfigure(encoding="utf-8")
from config import DBPassword
conn = mysql.connector.connect(host="localhost", user="root", password=DBPassword, database="tania_db")
c = conn.cursor(dictionary=True)

print("=== tania_temp_orders (all rows) ===")
c.execute("SELECT * FROM tania_temp_orders ORDER BY created_at DESC LIMIT 10")
rows = c.fetchall()
if rows:
    for r in rows:
        print(json.dumps({k: str(v) if v is not None else None for k,v in r.items()}, ensure_ascii=False))
else:
    print("EMPTY - no orders saved")

print("\n=== tania_temp_customers ===")
c.execute("SELECT * FROM tania_temp_customers ORDER BY created_at DESC LIMIT 5")
for r in c.fetchall():
    print(json.dumps({k: str(v) if v is not None else None for k,v in r.items()}, ensure_ascii=False))

print("\n=== tania_temp_addresses ===")
c.execute("SELECT * FROM tania_temp_addresses ORDER BY created_at DESC LIMIT 5")
for r in c.fetchall():
    print(json.dumps({k: str(v) if v is not None else None for k,v in r.items()}, ensure_ascii=False))

conn.close()
