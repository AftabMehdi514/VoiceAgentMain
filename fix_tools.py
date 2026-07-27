import re

with open('tools.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Remove the cache entirely
content = content.replace('# Cached products\n_products_cache = None\n', '')
content = content.replace('global _products_cache\n    \n    ', '')
content = content.replace('_products_cache = products', '')

# Adjust get_price to always call get_products and use it directly instead of _products_cache
get_price_func = '''def get_price(product_id, quantity):
    qty = quantity if quantity else 1
    
    products = get_products()
    if isinstance(products, dict) and "error" in products:
        products = []
        
    unit_price = 5.0 # fallback
    if products:
        for p in products:
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
    }'''

content = re.sub(r'def get_price\(.*?\n        "currency": "SAR"\n    \}', get_price_func, content, flags=re.DOTALL)

with open('tools.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Cache removed from tools.py")