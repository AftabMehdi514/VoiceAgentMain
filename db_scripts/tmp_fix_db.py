import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
﻿import sys, mysql.connector
sys.stdout.reconfigure(encoding="utf-8")
from config import DBPassword
conn = mysql.connector.connect(host="localhost", user="root", password=DBPassword, database="tania_db")
c = conn.cursor()

try:
    c.execute("ALTER TABLE tania_temp_orders DROP FOREIGN KEY fk_order_customer")
    print("Dropped fk_order_customer")
except Exception as e:
    print("Error dropping fk_order_customer:", e)

try:
    c.execute("ALTER TABLE tania_temp_orders DROP FOREIGN KEY fk_order_address")
    print("Dropped fk_order_address")
except Exception as e:
    print("Error dropping fk_order_address:", e)

conn.close()
