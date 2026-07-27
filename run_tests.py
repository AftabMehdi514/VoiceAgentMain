import json
import time
from tania_agent import process_turn, fresh_state

test_cases = [
    ("Riyadh (landmark only)", "I need 10 gallons of water delivered to Riyadh, Al Olaya district. I don't know the street name, but the nearest landmark is Kingdom Centre, and the building number is 1."),
    ("Riyadh (street)", "Please deliver 5 gallons of water to Riyadh, Al Malaz district, Salahuddin Al Ayyubi Road, building 24."),
    ("Jeddah (landmark)", "I need 8 gallons delivered to Jeddah, Al Zahra district. The closest landmark is Red Sea Mall, building number 15."),
    ("Jeddah (street + apartment)", "Deliver 12 gallons to Jeddah, Al Rawdah district, Prince Sultan Road, apartment 302."),
    ("Dammam", "I need 6 gallons delivered to Dammam, Al Faisaliyah district, Omar Ibn Al Khattab Street, building 9."),
    ("Khobar", "Please deliver 20 gallons to Al Khobar, Al Ulaya district. The nearest landmark is Al Rashid Mall, building number 44."),
    ("Makkah", "Deliver 4 gallons to Makkah, Al Aziziyah district, Abdullah Khayyat Street, building 11."),
    ("Madinah", "I need 10 gallons delivered to Madinah, Al Qiblatayn district. The nearest landmark is Quba Mosque, building number 7."),
    ("Abha", "Deliver 5 gallons to Abha, Al Mansak district, King Fahd Road, building 18."),
    ("Tabuk", "I need 15 gallons delivered to Tabuk, Al Muruj district, King Khalid Road, building 32."),
    ("Hail", "Please deliver 7 gallons to Hail, Al Samra district. The nearest landmark is Garden Mall, building number 6."),
    ("Buraidah", "Deliver 9 gallons to Buraidah, Al Iskan district, King Abdulaziz Road, building 20."),
    ("Jazan", "I need 10 gallons delivered to Jazan, Al Shati district. The nearest landmark is Rashid Mall Jazan, building number 12."),
    ("Taif", "Deliver 3 gallons to Taif, Al Faisaliyah district, Shubra Street, building 27."),
    ("Yanbu", "Please deliver 6 gallons to Yanbu, Al Bahr district. The nearest landmark is Yanbu Waterfront, building number 4."),
    ("Edge cases: Building + Landmark only", "I need 10 gallons delivered to Riyadh, Al Yasmin district. The nearest landmark is Riyadh Park Mall, building number 8."),
    ("Edge cases: Apartment instead of building", "Deliver 10 gallons to Jeddah, Al Salamah district, Sari Street, apartment 405."),
    ("Edge cases: Street only (no landmark)", "I need 10 gallons delivered to Dammam, Al Shati district, Prince Mohammed Bin Fahd Road, building 16."),
    ("Edge cases: Landmark only (no street)", "Deliver 10 gallons to Al Khobar, Al Aqrabiyah district. The nearest landmark is Venicia Mall, building number 3.")
]

output_md = "# Bulk Test Results for Address Resolution\n\n"

for idx, (title, input_text) in enumerate(test_cases):
    print(f"[{idx+1}/{len(test_cases)}] Running test: {title}...")
    state = fresh_state()
    history = []
    state['language'] = 'en'
    
    try:
        state, agent_reply, decision = process_turn(input_text, state, history)
        
        output_md += f"## {title}\n"
        output_md += f"**User Input:** `{input_text}`\n\n"
        
        llm_step = next((s for s in state.get('debug_log', []) if s['step'] == 'llm_candidate_selection'), None)
        
        if llm_step:
            full_prompt = llm_step['data'].get('full_llm_prompt', '')
            llm_json = llm_step['data'].get('llm_json_response') or {}
            reasoning = llm_json.get('reasoning', 'N/A')
            candidate_name = llm_json.get('candidate_name', 'None')
            selected_id = llm_json.get('selected_place_id', 'None')
            
            output_md += f"### Full Prompt Sent to LLM\n```text\n{full_prompt}\n```\n\n"
            output_md += f"### LLM Response & Reasoning\n"
            output_md += f"**Reasoning:** {reasoning}\n\n"
            output_md += f"**Chosen Candidate:** {candidate_name} (ID: {selected_id})\n\n"
        else:
            output_md += f"### LLM Prompt & Reasoning\n*LLM was not triggered (likely 0 or 1 candidate returned by OSM, or auto-resolved).* \n\n"
            
        output_md += f"### Finalized State (Slots)\n```json\n{json.dumps(state.get('address_slots', {}), indent=2, ensure_ascii=False)}\n```\n\n"
        
        output_md += f"### Agent Final Reply\n> {agent_reply}\n\n"
    
        output_md += "---\n\n"
        
    except Exception as e:
        output_md += f"## {title}\n"
        output_md += f"**ERROR:** {str(e)}\n\n---\n\n"

with open("Notes/tests.md", "w", encoding="utf-8") as f:
    f.write(output_md)

print("Done! Wrote results to Notes/tests.md")
