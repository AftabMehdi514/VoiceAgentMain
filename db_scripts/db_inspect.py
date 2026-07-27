import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import mysql.connector
import json

from config import DBPassword

def inspect_db():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password=DBPassword,
            database="tania_db"
        )
        cursor = conn.cursor(dictionary=True)
        
        tables = ['customers', 'addresses', 'products', 'tania_temp_customers', 'tania_temp_addresses', 'tania_temp_orders']
        
        schema = {}
        for t in tables:
            try:
                cursor.execute(f"DESCRIBE {t}")
                rows = cursor.fetchall()
                schema[t] = rows
            except Exception as e:
                schema[t] = f"Error: {e}"
        
        print(json.dumps(schema, indent=2, default=str))
        conn.close()
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == '__main__':
    inspect_db()