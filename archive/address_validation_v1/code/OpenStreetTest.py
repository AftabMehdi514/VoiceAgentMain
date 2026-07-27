from geocoder import resolve_address
import json
import sys

# Fix for printing Arabic characters in Windows terminal
sys.stdout.reconfigure(encoding='utf-8')

def test_geocoder():
    # Example 1: Successful Address
    print("--- Test 1: Successful Address ---")
    slots_valid = {
        "city": "الرياض",
        "district": "حي الورود",
        "street": "طريق الملك فهد"
    }
    result = resolve_address(slots_valid)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # Example 2: Major Street
    print("\n--- Test 2: Major Street ---")
    slots_street = {
        "city": "الرياض",
        "street": "طريق الملك فهد"
    }
    result = resolve_address(slots_street)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # Example 3: English Address
    print("\n--- Test 3: English Address ---")
    slots_english = {
        "city": "Jeddah",
        "district": "Al Balad"
    }
    result = resolve_address(slots_english)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    test_geocoder()
