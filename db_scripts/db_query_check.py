import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import mysql.connector
import json
from config import DBPassword

def run_checks():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password=DBPassword,
            database="tania_db"
        )
        cursor = conn.cursor(dictionary=True)
        
        print("=== PRODUCT SAMPLES (status=1, show_in_oms=1) ===")
        cursor.execute("""
            SELECT product_id, product_name, product_name_ar, price_vat, unit, for_whatsapp, show_in_oms
            FROM products
            WHERE status = 1 AND show_in_oms = 1
            ORDER BY product_sort_cc
            LIMIT 5
        """)
        for r in cursor.fetchall():
            print(json.dumps({k: str(v) if v is not None else None for k, v in r.items()}, ensure_ascii=False))
        
        print("\n=== COLUMN NAMES in products ===")
        cursor.execute("DESCRIBE products")
        cols = [r['Field'] for r in cursor.fetchall()]
        print(cols)
        
        print("\n=== COLUMN NAMES in addresses ===")
        cursor.execute("DESCRIBE addresses")
        cols = [r['Field'] for r in cursor.fetchall()]
        print(cols)
        
        print("\n=== ADDRESS SAMPLES (first 3 rows) ===")
        cursor.execute("""
            SELECT * FROM addresses ORDER BY updated_at DESC LIMIT 3
        """)
        for r in cursor.fetchall():
            print(json.dumps({k: str(v) if v is not None else None for k, v in r.items()}, ensure_ascii=False))
        
        print("\n=== CUSTOMER SAMPLE with mobile 966533621446 ===")
        cursor.execute("SELECT customer_id, name, mobile, email FROM customers WHERE mobile = '966533621446' LIMIT 1")
        r = cursor.fetchone()
        if r:
            cust_id = r['customer_id']
            print(json.dumps({k: str(v) if v is not None else None for k, v in r.items()}, ensure_ascii=False))
            
            print(f"\n=== ADDRESS for customer_id={cust_id} ===")
            cursor.execute("SELECT * FROM addresses WHERE customer_id = %s ORDER BY updated_at DESC LIMIT 2", (cust_id,))
            for row in cursor.fetchall():
                print(json.dumps({k: str(v) if v is not None else None for k, v in row.items()}, ensure_ascii=False))
        else:
            print("Customer not found")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

run_checks()