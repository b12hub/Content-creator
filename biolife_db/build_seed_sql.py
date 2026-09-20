#!/usr/bin/env python3
"""Generate 03_seed.sql from seed_data.json (single source of truth).
Usage: python3 build_seed_sql.py [seed_data.json] [03_seed.sql]
Fails loudly if any cross-reference (event_key, framework_name, dish key) is broken."""
import json, sys

src = sys.argv[1] if len(sys.argv) > 1 else "seed_data.json"
dst = sys.argv[2] if len(sys.argv) > 2 else "03_seed.sql"
d = json.load(open(src, encoding="utf-8"))

def q(v):
    """SQL literal."""
    if v is None: return "NULL"
    if isinstance(v, bool): return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)): return repr(v)
    if isinstance(v, (dict, list)):
        return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"

def arr(values, cast):
    if not values: return f"'{{}}'::{cast}[]"
    return "ARRAY[" + ", ".join(q(v) for v in values) + f"]::{cast}[]"

# ---- referential checks -------------------------------------------------
fw = {f["framework_name"] for f in d["psychological_frameworks"]}
ev = {e["key"] for e in d["cultural_events_seasons"]}
dish = {x["key"] for x in d["national_dishes_pairing"]}
errs = []
if sum(f["is_default"] for f in d["psychological_frameworks"]) != 1: errs.append("exactly one default framework required")
for e in d["cultural_events_seasons"]:
    for k in e.get("metadata", {}).get("featured_dish_keys", []):
        if k not in dish: errs.append(f"event {e['key']}: unknown dish {k}")
for m in d["event_framework_mappings"]:
    if m["event_key"] not in ev: errs.append(f"mapping: unknown event {m['event_key']}")
    if m["framework_name"] not in fw: errs.append(f"mapping: unknown framework {m['framework_name']}")
for e in d["cultural_events_seasons"]:
    if not any(m["event_key"] == e["key"] for m in d["event_framework_mappings"]):
        errs.append(f"event {e['key']} has no framework mapping")
for o in d["event_occurrences"]:
    if o["event_key"] not in ev: errs.append(f"occurrence: unknown event {o['event_key']}")
for g in d["annual_calendar_grid"]:
    if g["active_framework"] not in fw: errs.append(f"grid {g['month_number']}: unknown framework")
    for k in g["default_dish_keys"]:
        if k not in dish: errs.append(f"grid {g['month_number']}: unknown dish {k}")
if sorted(g["month_number"] for g in d["annual_calendar_grid"]) != list(range(1, 13)): errs.append("grid must have months 1..12")
for r in d["inference_rules"]:
    if "framework" in r["action"] and r["action"]["framework"] not in fw: errs.append(f"rule {r['rule_key']}: unknown framework")
    for k in r["condition"].get("event_key_in", []):
        if k not in ev: errs.append(f"rule {r['rule_key']}: unknown event {k}")
if errs:
    sys.exit("REFERENCE ERRORS:\n  " + "\n  ".join(errs))

# ---- SQL -----------------------------------------------------------------
out = ["-- File 03: SEED DATA (GENERATED from seed_data.json by build_seed_sql.py - do not edit by hand)",
       "BEGIN;", "SET search_path TO biolife, public;", ""]

out.append("-- psychological_frameworks")
for f in d["psychological_frameworks"]:
    out.append("INSERT INTO psychological_frameworks (framework_name, neural_target, primary_trigger, narrative_tone, visual_style, voiceover_guidelines, instagram_format, is_default) VALUES ("
               + ", ".join([q(f["framework_name"]), q(f["neural_target"]), q(f["primary_trigger"]), q(f["narrative_tone"]),
                            q(f["visual_style"]), q(f["voiceover_guidelines"]), q(f["instagram_format"]), q(f["is_default"])]) + ");")

out.append("\n-- national_dishes_pairing")
for x in d["national_dishes_pairing"]:
    out.append("INSERT INTO national_dishes_pairing (key, dish_name_uz, dish_name_ru, dish_category, caloric_density, serving_temperature, fat_and_spice_profile, physiological_impact, biolife_pairing_mechanics, recommended_sku, alternative_skus, typical_occasions) VALUES ("
               + ", ".join([q(x["key"]), q(x["dish_name_uz"]), q(x["dish_name_ru"]), q(x["dish_category"]), q(x["caloric_density"]),
                            q(x["serving_temperature"]), q(x["fat_and_spice_profile"]), q(x["physiological_impact"]),
                            q(x["biolife_pairing_mechanics"]), q(x["recommended_sku"]),
                            arr(x["alternative_skus"], "biolife.sku_type"), arr(x["typical_occasions"], "text")]) + ");")

out.append("\n-- cultural_events_seasons")
for e in d["cultural_events_seasons"]:
    w = e.get("window", {}); h = e.get("hijri", {})
    out.append("INSERT INTO cultural_events_seasons (key, name_uz, name_ru, temporal_anchor, anchor_type, start_month, start_day, end_month, end_day, hijri_month, hijri_day_start, hijri_day_end, priority, physiological_state, emotional_state, cultural_rituals, semiotic_symbols, content_guardrails, metadata) VALUES ("
               + ", ".join([q(e["key"]), q(e["name_uz"]), q(e["name_ru"]), q(e["temporal_anchor"]), q(e["anchor_type"]),
                            q(w.get("start_month")), q(w.get("start_day")), q(w.get("end_month")), q(w.get("end_day")),
                            q(h.get("month")), q(h.get("day_start")), q(h.get("day_end")), q(e["priority"]),
                            q(e["physiological_state"]), q(e["emotional_state"]),
                            arr(e["cultural_rituals"], "text"), arr(e["semiotic_symbols"], "text"),
                            q(e.get("content_guardrails", {})), q(e.get("metadata", {}))]) + ");")

out.append("\n-- event_occurrences")
for o in d["event_occurrences"]:
    out.append("INSERT INTO event_occurrences (event_id, year, start_date, end_date, is_confirmed, source) "
               f"SELECT id, {o['year']}, {q(o['start_date'])}, {q(o['end_date'])}, {q(o['is_confirmed'])}, {q(o['source'])} "
               f"FROM cultural_events_seasons WHERE key = {q(o['event_key'])};")

out.append("\n-- event_framework_mappings")
for m in d["event_framework_mappings"]:
    out.append("INSERT INTO event_framework_mappings (event_id, framework_id, selection_rationale, weight) "
               f"SELECT e.id, f.id, {q(m['selection_rationale'])}, {m['weight']} FROM cultural_events_seasons e, psychological_frameworks f "
               f"WHERE e.key = {q(m['event_key'])} AND f.framework_name = {q(m['framework_name'])};")

out.append("\n-- annual_calendar_grid")
for g in d["annual_calendar_grid"]:
    keys = g["default_dish_keys"]
    dish_arr = ("ARRAY(SELECT n.id FROM unnest(" + arr(keys, "text") + ") WITH ORDINALITY k(key, ord) "
                "JOIN national_dishes_pairing n ON n.key = k.key ORDER BY k.ord)")
    out.append("INSERT INTO annual_calendar_grid (month_number, season_name, average_temperature_range, avg_temp_min_c, avg_temp_max_c, primary_cultural_context, default_dish_pairings, active_framework_id) "
               f"SELECT {g['month_number']}, {q(g['season_name'])}, {q(g['average_temperature_range'])}, {g['avg_temp_min_c']}, {g['avg_temp_max_c']}, "
               f"{q(g['primary_cultural_context'])}, {dish_arr}, f.id FROM psychological_frameworks f WHERE f.framework_name = {q(g['active_framework'])};")

out.append("\n-- inference_rules")
for r in d["inference_rules"]:
    out.append("INSERT INTO inference_rules (rule_key, description, priority, condition, action) VALUES ("
               + ", ".join([q(r["rule_key"]), q(r["description"]), q(r["priority"]), q(r["condition"]), q(r["action"])]) + ");")

out += ["", "COMMIT;", ""]
open(dst, "w", encoding="utf-8").write("\n".join(out))
print(f"OK: wrote {dst}")
