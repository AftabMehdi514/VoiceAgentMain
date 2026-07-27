# Address Validation v1 (Archived)

This directory contains the original Phase 0 address validation implementation, which was archived in July 2026 to favor a simplified open-ended address flow.

## What each tier did and how
- **Tier 1 (Gazetteer Matching)**: Matched customer inputs against known city/district mappings, applying RapidFuzz-based candidate pooling.
- **Tier 2 (Mosque Landmark Matching)**: Attempted to match landmarks to a table of mosques (e.g. `uniquemosques`) to infer city and district.
- **Tier 3 (OSM/Nominatim Fallback)**: The fully implemented geocoder fallback, which issued requests to OpenStreetMap's Nominatim service, handled query relaxation, and performed LLM-based disambiguation for tied candidates.

## Data-quality Findings
- The `locations` and `locations_1` tables had no reliable relationship, making it difficult to do standard relational lookups.
- The `locations` table contained junk rows that would skew fuzzy matching.
- The `uniquemosques` table had sparse city fields, meaning matching a mosque didn't reliably give the rest of the address.
- We discovered an invalid-JSON row in the database during geocoding evaluation.

## Open Questions Unanswered by the Team
- How to properly clean the internal locations dataset?
- Whether OSM API rate limits and accuracy in Saudi Arabia are suitable for production load.
- How to handle highly ambiguous "popular" street names when no district is specified and Tier 1 data is sparse.

## How to re-integrate it later
The insertion point for this logic is the simplified flow's "new address" path. In the new flow, the agent simply extracts a single open address string. To re-activate this logic:
1. Re-import `geocoder.py` in `tania_agent.py`.
2. Pass the extracted address into `classify_and_merge`.
3. Re-enable the 9-slot state management in `state.py`.
4. Re-wire the geocoder intercept logic inside `process_turn()` and `generate_response()` (see the `# ARCHIVED 2026-07` markers in `tania_agent.py`).
