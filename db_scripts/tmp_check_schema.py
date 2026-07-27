import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
﻿import sys, mysql.connector
sys.stdout.reconfigure(encoding="utf-8")
from config import DBPassword
conn = mysql.connector.connect(host="localhost", user="root", password=DBPassword, database="tania_db")
c = conn.cursor(dictionary=True)

c.execute("SHOW CREATE TABLE tania_temp_orders")
row = c.fetchone()
print(row["Create Table"])

conn.close()
