import re

with open('tools.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Update get_latest_address
content = content.replace(
    '''cursor.execute("SELECT customer_id, address, map_info FROM addresses WHERE customer_id = %s ORDER BY updated_at DESC LIMIT 1", (customer_id,))''',
    '''cursor.execute("SELECT id, address, map_info FROM addresses WHERE customer_id = %s ORDER BY updated_at DESC LIMIT 1", (customer_id,))'''
)
content = content.replace(
    '''return {"found": True, "address": row['address'], "map_info": row['map_info']}''',
    '''return {"found": True, "address": row['address'], "map_info": row['map_info'], "address_id": row.get('id', row.get('temp_address_id'))}'''
)

# Update save_customer_and_address
save_addr_target = '''        # Insert address
        cursor.execute(
            "INSERT INTO tania_temp_addresses (temp_customer_id, address) VALUES (%s, %s)",
            (temp_customer_id, address_text)
        )
        temp_address_id = cursor.lastrowid
        
        conn.commit()
        return {"success": True, "temp_customer_id": temp_customer_id, "temp_address_id": temp_address_id}'''

save_addr_replacement = '''        # Insert address
        import json
        default_map_info = json.dumps({"latitude": "0.000000", "longitude": "0.000000"})
        cursor.execute(
            "INSERT INTO tania_temp_addresses (temp_customer_id, address, map_info) VALUES (%s, %s, %s)",
            (temp_customer_id, address_text, default_map_info)
        )
        temp_address_id = cursor.lastrowid
        
        conn.commit()
        return {"success": True, "temp_customer_id": temp_customer_id, "temp_address_id": temp_address_id, "map_info": default_map_info}'''

content = content.replace(save_addr_target, save_addr_replacement)

# Update create_order
content = content.replace(
    'def create_order(temp_customer_id, temp_address_id, items, total_amount, payment_method):',
    'def create_order(customer_id, address_id, items, total_amount, payment_method):'
)
content = content.replace(
    'INSERT INTO tania_temp_orders (temp_customer_id, temp_address_id, items, total_amount, payment_method, status) VALUES (%s, %s, %s, %s, %s, \\'confirmed\\')',
    'INSERT INTO tania_temp_orders (temp_customer_id, temp_address_id, items, total_amount, payment_method, status) VALUES (%s, %s, %s, %s, %s, \\'confirmed\\')'
)
content = content.replace(
    '(temp_customer_id, temp_address_id, items_json, total_amount, payment_method)',
    '(customer_id, address_id, items_json, total_amount, payment_method)'
)

with open('tools.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("tools.py updated successfully.")