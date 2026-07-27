import re

with open('tania_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Comment out import
content = content.replace('from geocoder import classify_and_merge', '# ARCHIVED 2026-07 — see /archive/address_validation_v1/README.md to reactivate\n# from geocoder import classify_and_merge')

# Comment out the geocoder parts in process_turn
# We can find the start of 'if text_to_classify or candidate_set_override:' and the end of the block.
# Actually, the entire block from 'new_info = decision.get(' to 'state["geocoder_status"] = status'
# should be commented out or removed.
# Let's replace the whole process_turn function since it's cleaner.

import re
process_turn_pattern = r'def process_turn\(customer_input, state, history\):.*?agent_reply = generate_response\(state, decision\)\n    return state, agent_reply, decision'
new_process_turn = '''def process_turn(customer_input, state, history):
    decision = extract_order_decision(customer_input, state, history)

    # ARCHIVED 2026-07 — see /archive/address_validation_v1/README.md to reactivate
    # (Removed Tie discriminator override and optional mismatch resets)

    state = update_state(state, decision, customer_message=customer_input)

    # ARCHIVED 2026-07 — see /archive/address_validation_v1/README.md to reactivate
    # (Removed geocoder slot classification logic here)

    agent_reply = generate_response(state, decision)
    return state, agent_reply, decision'''

content = re.sub(process_turn_pattern, new_process_turn, content, flags=re.DOTALL)


# Comment out missing_slot_question geocoder parts
missing_slot_pattern = r'    has_any_address = any\(slots\[k\].get\("value"\).*?return None'
new_missing_slot = '''    # ARCHIVED 2026-07 — see /archive/address_validation_v1/README.md to reactivate
    # (Removed 9-slot address prompting logic)
    
    if not state.get("address"):
        state["last_slot_prompted"] = "open_address"
        return (
            "ما هو عنوان التوصيل؟"
            if lang == "ar"
            else "What is the delivery address?"
        )
        
    return None'''
content = re.sub(missing_slot_pattern, new_missing_slot, content, flags=re.DOTALL)


# Fix generate_response
generate_response_pattern = r'    # Tie Discrimination Intercept.*?if question:\n        return question'
new_generate_response = '''    # ARCHIVED 2026-07 — see /archive/address_validation_v1/README.md to reactivate
    # (Removed Tie Discrimination Intercept, optional mismatch, geocoder status responses, and revalidation logic)

    if state["quantity"] and state.get("address"):
        if lang == "ar":
            return (
                f"للتأكيد: طلبك هو {state['quantity']} عبوة "
                f"إلى {state['address']}، "
                f"هل هذا صحيح؟"
            )
        return (
            f"To confirm: {state['quantity']} bottles "
            f"to {state['address']}. Is that correct?"
        )

    question = missing_slot_question(state)
    if question:
        return question'''
content = re.sub(generate_response_pattern, new_generate_response, content, flags=re.DOTALL)

with open('tania_agent.py', 'w', encoding='utf-8') as f:
    f.write(content)

# Now edit state.py
with open('state.py', 'r', encoding='utf-8') as f:
    state_content = f.read()

# Remove EMPTY_ADDRESS and 9-slot stuff
empty_addr_pattern = r'EMPTY_ADDRESS = \{.*?\}\n'
state_content = re.sub(empty_addr_pattern, '# ARCHIVED 2026-07 — see /archive/address_validation_v1/README.md to reactivate\n# (Removed EMPTY_ADDRESS and 9-slot schema)\nEMPTY_ADDRESS = {}\n', state_content, flags=re.DOTALL)

# Modify fresh_state
fresh_state_pattern = r'        "address_slots": dict\(EMPTY_ADDRESS\),.*?        "debug_log": \[\],'
new_fresh_state = '''        "address_slots": dict(EMPTY_ADDRESS),
        "address": None,
        # ARCHIVED 2026-07 — see /archive/address_validation_v1/README.md to reactivate
        # (Removed geocoder state fields like resolved_address, pending_geocode, geocoder_status, etc.)
        "last_slot_prompted": None,
        "confirmed": False,
        "language": "ar",'''
state_content = re.sub(fresh_state_pattern, new_fresh_state, state_content, flags=re.DOTALL)

# Modify update_state
update_state_pattern = r'    # For address, update_state now relies.*?decision\["intent"\] = "update_address"'
new_update_state = '''    # ARCHIVED 2026-07 — see /archive/address_validation_v1/README.md to reactivate
    # (Removed 9-slot assignment and geocoder confirmation intercept)
    
    if decision.get("new_address_info"):
        state["address"] = decision.get("new_address_info")
        state["last_slot_prompted"] = None'''
state_content = re.sub(update_state_pattern, new_update_state, state_content, flags=re.DOTALL)

# Update confirm check in update_state
confirm_pattern = r'and state\["address_slots"\].get\("city", \{\}\).get\("value"\)\n        and state.get\("resolved_address"\)'
new_confirm = '''and state.get("address")'''
state_content = re.sub(confirm_pattern, new_confirm, state_content)

with open('state.py', 'w', encoding='utf-8') as f:
    f.write(state_content)

print("Done")