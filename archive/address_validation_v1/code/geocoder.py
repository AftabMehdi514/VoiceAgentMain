import time
import requests
import json
import re
import os
from rapidfuzz import fuzz
from hf_client import qwen_chat
from utils import parse_first_json

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {
    "User-Agent": "TaniaWaterDeliveryBot/1.0 (admin@taniawater.local)",
    "Referer": "http://taniawater.local"
}
TIE_MARGIN_THRESHOLD = 8

def normalize_for_comparison(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'^(al |al-|el |el-)', '', text)
    text = re.sub(r'[\'’\-]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace("centre", "center")
    text = re.sub(r'[أإآا]', 'ا', text)
    text = re.sub(r'[\u064B-\u0652]', '', text) # Remove Arabic tashkeel
    return text

def extract_slot_val(slots, key):
    val = slots.get(key)
    if isinstance(val, dict):
        return val.get("value")
    return val

def score_candidate(slots, candidate):
    address = candidate.get("address", {})
    weights = {
        "landmark": 40,
        "street": 30,
        "district": 15,
        "city": 10
    }
    
    score_sum = 0
    weight_sum = 0
    breakdown = {}
    
    landmark = extract_slot_val(slots, "landmark")
    if landmark:
        w = weights["landmark"]
        norm_cust = normalize_for_comparison(landmark)
        norm_cand = normalize_for_comparison(candidate.get("name", candidate.get("display_name", "")))
        sim = fuzz.token_sort_ratio(norm_cust, norm_cand)
        score_sum += sim * w
        weight_sum += w
        breakdown["landmark"] = sim

    street = extract_slot_val(slots, "street")
    if street:
        w = weights["street"]
        norm_cust = normalize_for_comparison(street)
        norm_cand = normalize_for_comparison(address.get("road", address.get("pedestrian", "")))
        sim = fuzz.token_sort_ratio(norm_cust, norm_cand)
        score_sum += sim * w
        weight_sum += w
        breakdown["street"] = sim
        
    district = extract_slot_val(slots, "district")
    if district:
        w = weights["district"]
        norm_cust = normalize_for_comparison(district)
        norm_cand = normalize_for_comparison(address.get("suburb", address.get("neighbourhood", address.get("residential", ""))))
        sim = fuzz.token_sort_ratio(norm_cust, norm_cand)
        score_sum += sim * w
        weight_sum += w
        breakdown["district"] = sim

    city = extract_slot_val(slots, "city")
    if city:
        w = weights["city"]
        norm_cust = normalize_for_comparison(city)
        norm_cand = normalize_for_comparison(address.get("city", address.get("town", address.get("village", ""))))
        sim = fuzz.token_sort_ratio(norm_cust, norm_cand)
        score_sum += sim * w
        weight_sum += w
        breakdown["city"] = sim

    final_score = (score_sum / weight_sum) if weight_sum > 0 else 0
    
    cust_bno = extract_slot_val(slots, "building_no") or extract_slot_val(slots, "apartment_no")
    osm_bno = address.get("house_number")
    bno_status = "unverified"
    if osm_bno:
        if cust_bno:
            sim = fuzz.ratio(str(cust_bno).lower(), str(osm_bno).lower())
            if sim > 80:
                bno_status = "verified"
            else:
                bno_status = "mismatch"
    
    return final_score, breakdown, bno_status

def get_nominatim_candidates(query, lang):
    params = {
        "q": query,
        "countrycodes": "sa",
        "addressdetails": 1,
        "accept-language": lang,
        "format": "jsonv2",
        "limit": 10
    }
    
    max_retries = 3
    delay = 1
    data = None
    for attempt in range(max_retries):
        try:
            response = requests.get(NOMINATIM_URL, headers=HEADERS, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                break
        except requests.RequestException:
            pass
        time.sleep(delay)
        delay *= 2
        
    time.sleep(1) # OSM rate limit
    return data or []

def apply_dominant_candidate(best_cand_info, current_slots, raw_text, debug_log):
    cand = best_cand_info["candidate"]
    address = cand.get("address", {})
    new_slots = {k: dict(v) if isinstance(v, dict) else v for k, v in current_slots.items()}
    
    writes = {}
    
    osm_city = address.get("city", address.get("town", address.get("village")))
    osm_district = address.get("suburb", address.get("neighbourhood", address.get("residential")))
    osm_street = address.get("road", address.get("pedestrian"))
    osm_landmark = cand.get("name", cand.get("display_name"))
    
    if osm_city and not extract_slot_val(new_slots, "city"):
        new_slots["city"] = {"value": osm_city, "source": "system_inferred"}
        writes["city"] = osm_city
    if osm_district and not extract_slot_val(new_slots, "district"):
        new_slots["district"] = {"value": osm_district, "source": "system_inferred"}
        writes["district"] = osm_district
    if osm_street and not extract_slot_val(new_slots, "street"):
        new_slots["street"] = {"value": osm_street, "source": "system_inferred"}
        writes["street"] = osm_street
        
    if osm_landmark and not extract_slot_val(new_slots, "landmark"):
        if fuzz.token_sort_ratio(normalize_for_comparison(raw_text), normalize_for_comparison(osm_landmark)) > 70:
            new_slots["landmark"] = {"value": raw_text, "source": "system_inferred"}
            writes["landmark"] = raw_text
            
    debug_log.append({
        "step": "slot_write_stage",
        "message": "Dominant candidate applied",
        "data": {"writes": writes, "new_slots": new_slots}
    })
    
    return {"status": "resolved", "slots": new_slots, "candidate": cand}

def handle_tie(tied_candidates, current_slots, raw_text, debug_log):
    fields_to_check = {
        "city": lambda c: c["candidate"].get("address", {}).get("city", c["candidate"].get("address", {}).get("town", "")),
        "district": lambda c: c["candidate"].get("address", {}).get("suburb", c["candidate"].get("address", {}).get("neighbourhood", "")),
        "street": lambda c: c["candidate"].get("address", {}).get("road", "")
    }
    
    agreements = {}
    disagreements = {}
    
    for field, extractor in fields_to_check.items():
        vals = set(extractor(c) for c in tied_candidates if extractor(c))
        if len(vals) <= 1:
            val = list(vals)[0] if vals else None
            if val:
                agreements[field] = val
        else:
            disagreements[field] = list(vals)
            
    auto_fills = {}
    for field, val in agreements.items():
        if not extract_slot_val(current_slots, field):
            current_slots[field] = {"value": val, "source": "system_inferred"}
            auto_fills[field] = val

    debug_log.append({
        "step": "tie_diff",
        "message": "Diffed tied candidates and auto-filled agreements",
        "data": {"agreements": agreements, "disagreements": disagreements, "auto_filled": auto_fills}
    })
    
    bno = extract_slot_val(current_slots, "building_no") or extract_slot_val(current_slots, "apartment_no")
    if bno:
        tagged_candidates = [c for c in tied_candidates if c["candidate"].get("address", {}).get("house_number")]
        if tagged_candidates:
            matches = [c for c in tagged_candidates if c["candidate"].get("address", {}).get("house_number") == str(bno)]
            if len(matches) == 1:
                debug_log.append({"step": "optional_field_check", "message": "Resolved via optional field", "data": {"field": "building_no", "value": bno}})
                return apply_dominant_candidate(matches[0], current_slots, raw_text, debug_log)
            elif len(matches) == 0:
                debug_log.append({"step": "optional_field_check", "message": "Optional field present but conflicted", "data": {}})
                return {
                    "status": "pending_optional_mismatch", 
                    "slots": current_slots, 
                    "tentative_candidate": tied_candidates[0]["candidate"],
                    "mismatch_field": "building_no",
                    "mismatch_value": bno
                }
        else:
            debug_log.append({"step": "optional_field_check", "message": "Optional field absent from all candidates, treated as inconclusive", "data": {}})

    required_fields = ["city", "district", "street"]
    for req in required_fields:
        if req in disagreements and not extract_slot_val(current_slots, req):
            debug_log.append({"step": "required_field_check", "message": f"Required field {req} is tie-breaker", "data": {"field": req}})
            return {
                "status": "pending_tie_discriminator",
                "slots": current_slots,
                "discriminator_field": req,
                "tied_candidates": [c["candidate"] for c in tied_candidates]
            }
            
    debug_log.append({"step": "fallback_resolution", "message": "Could not discriminate tie, choosing top scorer", "data": {}})
    return apply_dominant_candidate(tied_candidates[0], current_slots, raw_text, debug_log)

def check_stale_numeric_fields(candidate, current_slots, debug_log, current_attempt_id):
    bno_slot = current_slots.get("building_no", {})
    if isinstance(bno_slot, dict):
        bno_val = bno_slot.get("value")
        attempt_id = bno_slot.get("attempt_id", 0)
        
        if bno_val and attempt_id < current_attempt_id:
            osm_bno = candidate.get("address", {}).get("house_number")
            if osm_bno and str(osm_bno).lower() != str(bno_val).lower():
                debug_log.append({"step": "stale_field_revalidation", "message": "Stale numeric field conflicted with newly resolved candidate", "data": {"field": "building_no", "value": bno_val, "osm_value": osm_bno}})
                return {
                    "status": "pending_optional_mismatch", 
                    "slots": current_slots, 
                    "tentative_candidate": candidate,
                    "mismatch_field": "building_no",
                    "mismatch_value": bno_val
                }
            else:
                debug_log.append({"step": "stale_field_revalidation", "message": "Stale numeric field was valid or absent in candidate", "data": {"field": "building_no", "value": bno_val}})
    return None

def llm_candidate_selection(raw_text, candidates, lang, debug_log, customer_message=""):
    if not candidates:
        return None
        
    prompt = (
        "You are an expert geographic assistant determining which OpenStreetMap search result "
        "matches the user's intended location.\n\n"
        f"Original full message from customer: \"{customer_message}\"\n"
        f"Address components extracted: \"{raw_text}\"\n\n"
        "Here are the top candidates returned by the search engine:\n"
    )
    
    candidates_sent_to_llm = []
    for idx, c in enumerate(candidates):
        cand = c["candidate"]
        osm_id = cand.get("place_id")
        display_name = cand.get("display_name", "")
        address_dict = cand.get("address", {})
        prompt += f"\n--- Candidate {idx+1} ---\n"
        prompt += f"place_id: {osm_id}\n"
        prompt += f"Display Name: {display_name}\n"
        prompt += f"Address Details: {json.dumps(address_dict, ensure_ascii=False)}\n"
        
        candidates_sent_to_llm.append({
            "place_id": osm_id,
            "display_name": display_name
        })
        
    prompt += (
        "\nTask: Decide which 'place_id' best semantically matches the user's intent. "
        "Account for translations (e.g. English 'Panda' = Arabic 'بنده'). "
        "Use the full customer message to inform your decision (e.g., if they mention a landmark or a specific street context).\n"
        "1. Write a short explanation of what matched, what didn't match, and why you chose this candidate (or why it's ambiguous).\n"
        "2. If you are confident in one exact match, output its place_id and its display name.\n"
        "3. If they are ambiguous, or if none truly match the intent, output null for the place_id.\n"
        "RETURN ONLY VALID JSON in this exact format: {\"reasoning\": \"...\", \"selected_place_id\": 12345, \"candidate_name\": \"...\"} or {\"reasoning\": \"...\", \"selected_place_id\": null}\n"
        "Do not include markdown blocks or any other text. /no_think"
    )
    
    messages = [{"role": "system", "content": prompt}]
    
    try:
        raw_response = qwen_chat(messages, max_new_tokens=400)
        parsed = parse_first_json(raw_response)
        
        selected_id = None
        if parsed and "selected_place_id" in parsed:
            selected_id = parsed["selected_place_id"]
            if selected_id is not None:
                valid_ids = [c["candidate"].get("place_id") for c in candidates]
                if selected_id not in valid_ids:
                    selected_id = None
                    
        debug_log.append({
            "step": "llm_candidate_selection",
            "message": "LLM evaluated top candidates",
            "data": {
                "full_llm_prompt": prompt,
                "candidates_sent": candidates_sent_to_llm,
                "selected_place_id": selected_id,
                "raw_response": raw_response,
                "llm_json_response": parsed
            }
        })
        return selected_id
    except Exception as e:
        debug_log.append({
            "step": "llm_candidate_selection_error",
            "message": f"Error during LLM evaluation: {str(e)}",
            "data": {
                "full_llm_prompt": prompt,
                "candidates_sent": candidates_sent_to_llm
            }
        })
        
    return None

def classify_and_merge(raw_text, current_slots, lang, debug_log, candidate_set_override=None, current_attempt_id=1, customer_message=""):
    debug_log.append({
        "step": "raw_input_stage",
        "message": "Received raw text for classification",
        "data": {"raw_text": raw_text, "current_slots": dict(current_slots)}
    })
    
    country_suffix = "السعودية" if lang == "ar" else "Saudi Arabia"
    sep = "، " if lang == "ar" else ", "
    
    city_val = extract_slot_val(current_slots, "city")
    district_val = extract_slot_val(current_slots, "district")
    street_val = extract_slot_val(current_slots, "street")
    landmark_val = extract_slot_val(current_slots, "landmark")

    queries_to_try = []
    def add_query(*parts):
        parts_filtered = [p for p in parts if p]
        q = sep.join(parts_filtered)
        if q not in queries_to_try:
            queries_to_try.append(q)

    # Slot-based specific combinations (when slots are known)
    if any([landmark_val, street_val, district_val, city_val]):
        add_query(raw_text, landmark_val, street_val, district_val, city_val, country_suffix)
        if district_val and city_val:
            add_query(raw_text, district_val, city_val, country_suffix)
        if city_val:
            add_query(raw_text, city_val, country_suffix)
            
    # String-based combinations (for raw text with commas)
    raw_segments = [s.strip() for s in re.split(r'[,،]', raw_text) if s.strip()]
    
    # 1. Reversed order (Specific -> General, Nominatim's preference)
    rev_segments = list(reversed(raw_segments))
    for i in range(len(rev_segments)):
        add_query(*rev_segments[i:], country_suffix)
        
    # 2. Original order (General -> Specific)
    for i in range(len(raw_segments)):
        add_query(*raw_segments[i:], country_suffix)
        
    # 3. Just the raw text as fallback
    add_query(raw_text, country_suffix)

    scoring_slots = {k: v for k, v in current_slots.items()}
    for field in ["landmark", "street", "district", "city"]:
        if not extract_slot_val(scoring_slots, field):
            scoring_slots[field] = {"value": raw_text, "source": "customer_stated"}

    candidate_pool = {}

    if candidate_set_override is not None:
        data = candidate_set_override
        debug_log.append({
            "step": "query_stage",
            "message": "Used override candidate set (tie discriminator)",
            "data": {"num_candidates": len(data)}
        })
        for cand in data:
            pid = cand.get("place_id")
            if pid:
                candidate_pool[pid] = cand
    else:
        for idx, q_variant in enumerate(queries_to_try):
            data = get_nominatim_candidates(q_variant, lang)
            debug_log.append({
                "step": "query_stage",
                "message": f"Issued query variant {idx+1}/{len(queries_to_try)}",
                "data": {"query": q_variant, "num_candidates": len(data)}
            })
            
            if data:
                for cand in data:
                    pid = cand.get("place_id")
                    if pid and pid not in candidate_pool:
                        candidate_pool[pid] = cand
                        
            if candidate_pool:
                scored_candidates = []
                for cand in candidate_pool.values():
                    score, _, _ = score_candidate(scoring_slots, cand)
                    scored_candidates.append({"score": score})
                scored_candidates.sort(key=lambda x: x["score"], reverse=True)
                
                margin = 0
                if len(scored_candidates) > 1:
                    margin = scored_candidates[0]["score"] - scored_candidates[1]["score"]
                
                # Check early stop
                if len(scored_candidates) == 1:
                    if scored_candidates[0]["score"] >= 70:
                        debug_log.append({"step": "early_stop", "message": "Stopped relaxing early", "data": {"margin": 0, "top_score": scored_candidates[0]["score"]}})
                        break
                else:
                    if margin >= TIE_MARGIN_THRESHOLD and scored_candidates[0]["score"] >= 70:
                        debug_log.append({"step": "early_stop", "message": "Stopped relaxing early", "data": {"margin": margin, "top_score": scored_candidates[0]["score"]}})
                        break

    if not candidate_pool:
        debug_log.append({
            "step": "no_candidates",
            "message": "No match found, marked unresolved-this-turn.",
            "data": {}
        })
        return {"status": "unresolved", "slots": current_slots}
        
    scored_candidates = []
    for cand in candidate_pool.values():
        score, breakdown, bno_status = score_candidate(scoring_slots, cand)
        scored_candidates.append({
            "candidate": cand,
            "score": score,
            "breakdown": breakdown,
            "bno_status": bno_status
        })
        
    scored_candidates.sort(key=lambda x: x["score"], reverse=True)
    
    margin = 0
    tied = False
    
    if len(scored_candidates) > 1:
        # LLM semantic evaluation of top 6 candidates
        top_candidates = scored_candidates[:6]
        selected_id = llm_candidate_selection(raw_text, top_candidates, lang, debug_log, customer_message=customer_message)
        
        if selected_id is not None:
            # LLM successfully chose a dominant candidate
            chosen_candidate = next((c for c in scored_candidates if c["candidate"].get("place_id") == selected_id), None)
            if chosen_candidate:
                debug_log.append({
                    "step": "llm_winner",
                    "message": f"LLM explicitly selected place_id: {selected_id}",
                    "data": {"display_name": chosen_candidate["candidate"].get("display_name")}
                })
                # Bypass margin check, treat as outright dominant
                res = apply_dominant_candidate(chosen_candidate, current_slots, raw_text, debug_log)
                stale_res = check_stale_numeric_fields(res["candidate"], res["slots"], debug_log, current_attempt_id)
                if stale_res:
                    return stale_res
                return res
        
        # Fallback to fuzzy logic if LLM returns null or fails
        margin = scored_candidates[0]["score"] - scored_candidates[1]["score"]
        tied = margin < TIE_MARGIN_THRESHOLD
        
    debug_log.append({
        "step": "scoring_stage",
        "message": "Scored candidates",
        "data": {
            "scored_candidates": [{"name": c["candidate"].get("display_name"), "score": c["score"]} for c in scored_candidates],
            "margin": margin,
            "is_tied": tied
        }
    })
    
    if not tied:
        res = apply_dominant_candidate(scored_candidates[0], current_slots, raw_text, debug_log)
        stale_res = check_stale_numeric_fields(res["candidate"], res["slots"], debug_log, current_attempt_id)
        if stale_res:
            return stale_res
        return res
    else:
        tied_candidates = [c for c in scored_candidates if scored_candidates[0]["score"] - c["score"] < TIE_MARGIN_THRESHOLD]
        res = handle_tie(tied_candidates, current_slots, raw_text, debug_log)
        if res.get("status") == "resolved":
            stale_res = check_stale_numeric_fields(res["candidate"], res["slots"], debug_log, current_attempt_id)
            if stale_res:
                return stale_res
        return res


