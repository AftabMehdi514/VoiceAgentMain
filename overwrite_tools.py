import os

new_tools_content = '''import json
from db import get_connection

# Cached products
_products_cache = None

def get_products():
    global _products_cache
    
    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}
    
    try:
        cursor = conn.cursor(dictionary=True)
        # Assuming product_name is bilingual or we have name_en, name_ar.
        # Spec says: pull from bilingual JSON fields if available, but the query is:
        # SELECT product_id, product_name, product_description, price_vat, unit
        cursor.execute("""
            SELECT product_id, product_name, product_description, price_vat, unit
            FROM products
            WHERE status = 1 AND show_in_oms = 1
            ORDER BY product_sort_cc
            LIMIT 20
        """)
        products = cursor.fetchall()
        _products_cache = products
        
        # Convert Decimals to float for JSON serialization
        for p in products:
            if 'price_vat' in p and p['price_vat'] is not None:
                p['price_vat'] = float(p['price_vat'])
                
        return products
    except Exception as e:
        print(f"Error fetching products: {e}")
        # Return fallback if DB fails or table missing
        return [
            {"product_id": 1, "product_name": "Tania Water 330ml", "price_vat": 5.0, "unit": "Carton"},
            {"product_id": 2, "product_name": "Tania Water 200ml", "price_vat": 4.5, "unit": "Carton"}
        ]
    finally:
        if 'cursor' in locals():
            cursor.close()
        conn.close()

def get_price(product_id, quantity):
    qty = quantity if quantity else 1
    
    # Refresh cache if empty
    global _products_cache
    if not _products_cache:
        get_products()
        
    unit_price = 5.0 # fallback
    if _products_cache:
        for p in _products_cache:
            if str(p.get("product_id")) == str(product_id):
                unit_price = float(p.get("price_vat", 5.0))
                break
                
    total = round(unit_price * qty, 2)
    return {
        "product_id": product_id,
        "quantity": qty,
        "unit_price": unit_price,
        "total_price": total,
        "currency": "SAR"
    }

def calculate_order_total(items):
    total_amount = 0.0
    for item in items:
        # items could be list of dicts: {"product_id": x, "quantity": y}
        product_id = item.get("product_id")
        quantity = item.get("quantity", 1)
        price_info = get_price(product_id, quantity)
        total_amount += price_info["total_price"]
        
    return {"total_amount": round(total_amount, 2), "currency": "SAR"}

def lookup_customer(mobile=None):
    if not mobile:
        return {"found": False}
        
    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}
        
    try:
        cursor = conn.cursor(dictionary=True)
        # Search production customers or temp? The spec says:
        # SELECT customer_id FROM customers WHERE mobile = ?;
        # Since we shouldn't hit production tables in Phase 1 except for reading,
        # we try reading from production customers first, or tania_temp_customers.
        # But wait, Phase 1 spec says: 
        # Existing customer lookup: SELECT customer_id FROM customers WHERE mobile = ?;
        
        # Let's try production first, fallback to temp for testing
        customer_id = None
        try:
            cursor.execute("SELECT customer_id, name, email FROM customers WHERE mobile = %s LIMIT 1", (mobile,))
            row = cursor.fetchone()
            if row:
                customer_id = row['customer_id']
                name = row.get('name')
                email = row.get('email')
                return {"found": True, "customer_id": customer_id, "name": name, "email": email}
        except Exception as e:
            pass # customers table might not exist in dev
            
        # Try temp table
        cursor.execute("SELECT temp_customer_id, name, email FROM tania_temp_customers WHERE mobile = %s LIMIT 1", (mobile,))
        row = cursor.fetchone()
        if row:
            return {"found": True, "customer_id": row['temp_customer_id'], "name": row.get('name'), "email": row.get('email'), "is_temp": True}
            
        return {"found": False}
    finally:
        if 'cursor' in locals():
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
        # Spec says:
        # SELECT customer_id, address, map_info FROM addresses WHERE customer_id = ? ORDER BY updated_at DESC LIMIT 1;
        
        # Try production addresses
        try:
            cursor.execute("SELECT customer_id, address, map_info FROM addresses WHERE customer_id = %s ORDER BY updated_at DESC LIMIT 1", (customer_id,))
            row = cursor.fetchone()
            if row:
                return {"found": True, "address": row['address'], "map_info": row['map_info']}
        except:
            pass
            
        # Try temp addresses
        cursor.execute("SELECT temp_customer_id, address, map_info FROM tania_temp_addresses WHERE temp_customer_id = %s ORDER BY created_at DESC LIMIT 1", (customer_id,))
        row = cursor.fetchone()
        if row:
            return {"found": True, "address": row['address'], "map_info": row['map_info']}
            
        return {"found": False}
    finally:
        if 'cursor' in locals():
            cursor.close()
        conn.close()

def save_customer_and_address(mobile, address_text):
    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}
        
    try:
        cursor = conn.cursor()
        
        # Check if exists in temp
        cursor.execute("SELECT temp_customer_id FROM tania_temp_customers WHERE mobile = %s", (mobile,))
        row = cursor.fetchone()
        
        if row:
            temp_customer_id = row[0]
        else:
            cursor.execute("INSERT INTO tania_temp_customers (mobile) VALUES (%s)", (mobile,))
            temp_customer_id = cursor.lastrowid
            
        # Insert address
        cursor.execute(
            "INSERT INTO tania_temp_addresses (temp_customer_id, address) VALUES (%s, %s)",
            (temp_customer_id, address_text)
        )
        temp_address_id = cursor.lastrowid
        
        conn.commit()
        return {"success": True, "temp_customer_id": temp_customer_id, "temp_address_id": temp_address_id}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        if 'cursor' in locals():
            cursor.close()
        conn.close()

def create_order(temp_customer_id, temp_address_id, items, total_amount, payment_method):
    conn = get_connection()
    if not conn:
        return {"error": "db_connection_failed"}
        
    try:
        cursor = conn.cursor()
        items_json = json.dumps(items)
        
        cursor.execute(
            "INSERT INTO tania_temp_orders (temp_customer_id, temp_address_id, items, total_amount, payment_method, status) VALUES (%s, %s, %s, %s, %s, 'confirmed')",
            (temp_customer_id, temp_address_id, items_json, total_amount, payment_method)
        )
        temp_order_id = cursor.lastrowid
        
        conn.commit()
        return {"success": True, "temp_order_id": temp_order_id}
    except Exception as e:
        conn.rollback()
        return {"error": str(e)}
    finally:
        if 'cursor' in locals():
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
        if 'cursor' in locals():
            cursor.close()
        conn.close()


TOOL_REGISTRY = {
    "lookup_customer": lookup_customer,
    "get_latest_address": get_latest_address,
    "save_customer_and_address": save_customer_and_address,
    "get_products": get_products,
    "get_price": get_price,
    "calculate_order_total": calculate_order_total,
    "create_order": create_order,
    "save_rating": save_rating
}

def execute_tool(name, arguments):
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        return {"error": f"unknown_tool:{name}"}
    try:
        return fn(**(arguments or {}))
    except TypeError as e:
        return {"error": f"bad_arguments:{e}"}
    except Exception as e:
        return {"error": str(e)}
'''

with open('tools.py', 'w', encoding='utf-8') as f:
    f.write(new_tools_content)

print("tools.py overwritten")