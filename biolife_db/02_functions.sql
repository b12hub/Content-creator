-- =====================================================================
-- File 02: RUNTIME INFERENCE (Section 3)
-- Main entry point for the AI copywriter:
--   SELECT biolife.resolve_ad_context(
--            p_date     => '2027-07-15',
--            p_temp_c   => 43,            -- live outdoor temp (optional)
--            p_dish_key => 'osh',         -- user-selected dish (optional)
--            p_trend    => 'ice bucket'); -- trending topic (optional)
-- Returns one JSONB "creative brief" the LLM prompt is built from.
-- =====================================================================
SET search_path TO biolife, public;

-- Does a (month, day) window contain date d? Handles year wrap (Dec 20 -> Jan 3).
CREATE OR REPLACE FUNCTION in_md_window(d DATE, sm INT, sd INT, em INT, ed INT)
RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
SET search_path = biolife, public
AS $$
  SELECT CASE
    WHEN sm*100+sd <= em*100+ed
      THEN (EXTRACT(MONTH FROM d)*100 + EXTRACT(DAY FROM d)) BETWEEN sm*100+sd AND em*100+ed
    ELSE (EXTRACT(MONTH FROM d)*100 + EXTRACT(DAY FROM d)) >= sm*100+sd
      OR (EXTRACT(MONTH FROM d)*100 + EXTRACT(DAY FROM d)) <= em*100+ed
  END
$$;

-- All events active on a date, strongest first.
-- A row in event_occurrences for that year overrides the recurring window.
CREATE OR REPLACE FUNCTION active_events(p_date DATE)
RETURNS TABLE (event_id INT, key TEXT, priority SMALLINT, source TEXT, is_confirmed BOOLEAN)
LANGUAGE sql STABLE
SET search_path = biolife, public
AS $$
  SELECT e.id, e.key, e.priority, 'occurrence', o.is_confirmed
  FROM cultural_events_seasons e
  JOIN event_occurrences o ON o.event_id = e.id
  WHERE e.is_active AND p_date BETWEEN o.start_date AND o.end_date
  UNION ALL
  SELECT e.id, e.key, e.priority, 'recurring_window', TRUE
  FROM cultural_events_seasons e
  WHERE e.is_active
    AND e.anchor_type <> 'HIJRI_LUNAR'
    AND in_md_window(p_date, e.start_month, e.start_day, e.end_month, e.end_day)
    AND NOT EXISTS (SELECT 1 FROM event_occurrences o
                    WHERE o.event_id = e.id AND o.year = EXTRACT(YEAR FROM p_date))
  ORDER BY 3 DESC, 2
$$;

-- Evaluate one inference_rules.condition object
CREATE OR REPLACE FUNCTION rule_matches(c JSONB, p_temp NUMERIC, p_month INT,
                                        p_event_keys TEXT[], p_trend TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE
SET search_path = biolife, public
AS $$
BEGIN
  IF c ? 'temp_gte' AND (p_temp IS NULL OR p_temp < (c->>'temp_gte')::numeric) THEN RETURN FALSE; END IF;
  IF c ? 'temp_lte' AND (p_temp IS NULL OR p_temp > (c->>'temp_lte')::numeric) THEN RETURN FALSE; END IF;
  IF c ? 'month_in' AND NOT (p_month = ANY (ARRAY(SELECT jsonb_array_elements_text(c->'month_in'))::int[])) THEN RETURN FALSE; END IF;
  IF c ? 'event_key_in' AND NOT (p_event_keys && ARRAY(SELECT jsonb_array_elements_text(c->'event_key_in'))) THEN RETURN FALSE; END IF;
  IF c ? 'trend_present' AND ((c->>'trend_present')::boolean <> (p_trend IS NOT NULL AND btrim(p_trend) <> '')) THEN RETURN FALSE; END IF;
  RETURN TRUE;
END $$;

-- make_date that clamps the day (Feb 29 in non-leap years -> Feb 28)
CREATE OR REPLACE FUNCTION safe_make_date(y INT, m INT, d INT)
RETURNS DATE LANGUAGE sql IMMUTABLE
SET search_path = biolife, public
AS $$
  SELECT make_date(y, m, 1)
       + (LEAST(d, EXTRACT(DAY FROM (make_date(y, m, 1) + INTERVAL '1 month - 1 day'))::int) - 1)
$$;

-- Concrete start/end of the event window that contains p_date (occurrence row wins)
CREATE OR REPLACE FUNCTION event_window(p_event_id INT, p_date DATE,
                                        OUT window_start DATE, OUT window_end DATE)
LANGUAGE plpgsql STABLE
SET search_path = biolife, public
AS $$
DECLARE e cultural_events_seasons%ROWTYPE; y INT := EXTRACT(YEAR FROM p_date);
BEGIN
  SELECT o.start_date, o.end_date INTO window_start, window_end
  FROM event_occurrences o
  WHERE o.event_id = p_event_id AND p_date BETWEEN o.start_date AND o.end_date
  LIMIT 1;
  IF window_start IS NOT NULL THEN RETURN; END IF;

  SELECT * INTO e FROM cultural_events_seasons WHERE id = p_event_id;
  IF e.anchor_type = 'HIJRI_LUNAR' OR e.id IS NULL THEN RETURN; END IF;
  IF e.start_month*100 + e.start_day <= e.end_month*100 + e.end_day THEN
    window_start := safe_make_date(y, e.start_month, e.start_day);
    window_end   := safe_make_date(y, e.end_month, e.end_day);
  ELSIF EXTRACT(MONTH FROM p_date)*100 + EXTRACT(DAY FROM p_date) >= e.start_month*100 + e.start_day THEN
    window_start := safe_make_date(y, e.start_month, e.start_day);       -- Dec part of a wrap
    window_end   := safe_make_date(y + 1, e.end_month, e.end_day);
  ELSE
    window_start := safe_make_date(y - 1, e.start_month, e.start_day);   -- Jan part of a wrap
    window_end   := safe_make_date(y, e.end_month, e.end_day);
  END IF;
END $$;

CREATE OR REPLACE FUNCTION resolve_ad_context(
  p_date     DATE    DEFAULT CURRENT_DATE,
  p_temp_c   NUMERIC DEFAULT NULL,
  p_dish_key TEXT    DEFAULT NULL,
  p_trend    TEXT    DEFAULT NULL
) RETURNS JSONB
LANGUAGE plpgsql STABLE
SET search_path = biolife, public
AS $$
DECLARE
  v_month       INT := EXTRACT(MONTH FROM p_date);
  v_grid        annual_calendar_grid%ROWTYPE;
  v_event       cultural_events_seasons%ROWTYPE;
  v_event_keys  TEXT[];
  v_fw_id       INT;
  v_fw          psychological_frameworks%ROWTYPE;
  v_fallback    TEXT;
  v_rationale   TEXT;
  v_temp        NUMERIC := p_temp_c;
  v_temp_source TEXT := 'live_input';
  v_dish_ids    INT[];
  v_dishes      JSONB;
  v_sku         TEXT;
  v_rule        RECORD;
  v_applied     JSONB := '[]'::jsonb;
  v_tone_mods   JSONB := '[]'::jsonb;
  v_visual_mods JSONB := '[]'::jsonb;
  v_warnings    JSONB := '[]'::jsonb;
  v_guardrails  JSONB := '[]'::jsonb;
  v_estimated   BOOLEAN := FALSE;
  v_wstart      DATE;
  v_wend        DATE;
BEGIN
  SELECT * INTO v_grid FROM annual_calendar_grid WHERE month_number = v_month;

  -- temperature: live input, else monthly average max
  IF v_temp IS NULL AND v_grid.month_number IS NOT NULL THEN
    v_temp := v_grid.avg_temp_max_c; v_temp_source := 'monthly_average_max';
  END IF;

  -- events
  SELECT array_agg(a.key ORDER BY a.priority DESC), bool_or(NOT a.is_confirmed)
    INTO v_event_keys, v_estimated
  FROM active_events(p_date) a;
  v_event_keys := COALESCE(v_event_keys, '{}');

  SELECT e.* INTO v_event
  FROM active_events(p_date) a JOIN cultural_events_seasons e ON e.id = a.event_id
  ORDER BY a.priority DESC, a.key LIMIT 1;

  IF v_event.id IS NOT NULL THEN
    SELECT w.window_start, w.window_end INTO v_wstart, v_wend FROM event_window(v_event.id, p_date) w;
  END IF;

  SELECT jsonb_agg(e.content_guardrails) INTO v_guardrails
  FROM cultural_events_seasons e
  WHERE e.key = ANY(v_event_keys) AND e.content_guardrails <> '{}'::jsonb;

  -- framework: event mapping -> month grid -> global default
  IF v_event.id IS NOT NULL THEN
    SELECT m.framework_id, m.selection_rationale INTO v_fw_id, v_rationale
    FROM event_framework_mappings m WHERE m.event_id = v_event.id
    ORDER BY m.weight DESC, m.framework_id LIMIT 1;
    IF v_fw_id IS NOT NULL THEN v_fallback := 'EVENT_MAPPING'; END IF;
  END IF;
  IF v_fw_id IS NULL AND v_grid.month_number IS NOT NULL THEN
    v_fw_id := v_grid.active_framework_id; v_fallback := 'MONTH_GRID';
    v_rationale := 'No mapped event; using monthly baseline framework.';
  END IF;
  IF v_fw_id IS NULL THEN
    SELECT id INTO v_fw_id FROM psychological_frameworks WHERE is_default;
    v_fallback := 'GLOBAL_DEFAULT';
    v_rationale := 'No event and no month grid row; using global default framework.';
  END IF;

  -- dishes: user choice -> event featured dishes -> month defaults
  IF p_dish_key IS NOT NULL THEN
    SELECT array_agg(id) INTO v_dish_ids FROM national_dishes_pairing WHERE key = p_dish_key;
    IF v_dish_ids IS NULL THEN
      v_warnings := v_warnings || to_jsonb('Unknown dish_key "' || p_dish_key || '"; fell back to defaults');
    END IF;
  END IF;
  IF v_dish_ids IS NULL AND v_event.metadata ? 'featured_dish_keys' THEN
    SELECT array_agg(n.id ORDER BY k.ord) INTO v_dish_ids
    FROM jsonb_array_elements_text(v_event.metadata->'featured_dish_keys') WITH ORDINALITY k(key, ord)
    JOIN national_dishes_pairing n ON n.key = k.key;
  END IF;
  IF v_dish_ids IS NULL THEN v_dish_ids := v_grid.default_dish_pairings; END IF;

  -- runtime rules (ascending priority; later = stronger)
  FOR v_rule IN SELECT * FROM inference_rules WHERE is_active ORDER BY priority, id LOOP
    IF rule_matches(v_rule.condition, v_temp, v_month, v_event_keys, p_trend) THEN
      v_applied := v_applied || to_jsonb(v_rule.rule_key);
      IF v_rule.action ? 'framework' THEN
        SELECT id INTO v_fw_id FROM psychological_frameworks
        WHERE framework_name = (v_rule.action->>'framework')::framework_type;
        v_rationale := COALESCE(v_rule.action->>'note', v_rule.description);
        v_fallback := 'RULE_OVERRIDE';
      END IF;
      IF v_rule.action ? 'sku'             THEN v_sku := v_rule.action->>'sku'; END IF;
      IF v_rule.action ? 'tone_modifier'   THEN v_tone_mods   := v_tone_mods   || (v_rule.action->'tone_modifier'); END IF;
      IF v_rule.action ? 'visual_modifier' THEN v_visual_mods := v_visual_mods || (v_rule.action->'visual_modifier'); END IF;
    END IF;
  END LOOP;

  SELECT * INTO v_fw FROM psychological_frameworks WHERE id = v_fw_id;

  SELECT COALESCE(jsonb_agg(jsonb_build_object(
           'key', n.key, 'name_uz', n.dish_name_uz, 'name_ru', n.dish_name_ru,
           'category', n.dish_category, 'caloric_density', n.caloric_density,
           'fat_and_spice_profile', n.fat_and_spice_profile,
           'physiological_impact', n.physiological_impact,
           'pairing_mechanics', n.biolife_pairing_mechanics,
           'recommended_sku', n.recommended_sku) ORDER BY k.ord), '[]'::jsonb)
    INTO v_dishes
  FROM unnest(v_dish_ids) WITH ORDINALITY k(id, ord)
  JOIN national_dishes_pairing n ON n.id = k.id;

  IF v_sku IS NULL THEN v_sku := v_dishes->0->>'recommended_sku'; END IF;
  IF v_estimated THEN
    v_warnings := v_warnings || to_jsonb('Lunar event date is an estimate - confirm with the Muslim Board of Uzbekistan before publishing'::text);
  END IF;

  RETURN jsonb_build_object(
    'input', jsonb_build_object('date', p_date, 'temp_c', p_temp_c, 'dish_key', p_dish_key, 'trend', p_trend),
    'resolved_temperature_c', v_temp,
    'temperature_source', v_temp_source,
    'season', v_grid.season_name,
    'month_context', v_grid.primary_cultural_context,
    'primary_event', CASE WHEN v_event.id IS NULL THEN NULL ELSE jsonb_build_object(
        'key', v_event.key, 'name_uz', v_event.name_uz, 'name_ru', v_event.name_ru,
        'priority', v_event.priority,
        'window_start', v_wstart, 'window_end', v_wend,
        'day_index', CASE WHEN v_wstart IS NULL THEN NULL ELSE p_date - v_wstart + 1 END,
        'days_total', CASE WHEN v_wstart IS NULL THEN NULL ELSE v_wend - v_wstart + 1 END,
        'physiological_state', v_event.physiological_state,
        'emotional_state', v_event.emotional_state,
        'cultural_rituals', to_jsonb(v_event.cultural_rituals),
        'semiotic_symbols', to_jsonb(v_event.semiotic_symbols)) END,
    'all_active_events', to_jsonb(v_event_keys),
    'framework', jsonb_build_object(
        'name', v_fw.framework_name, 'neural_target', v_fw.neural_target,
        'primary_trigger', v_fw.primary_trigger, 'narrative_tone', v_fw.narrative_tone,
        'visual_style', v_fw.visual_style, 'voiceover', v_fw.voiceover_guidelines,
        'instagram_format', v_fw.instagram_format,
        'selection_rationale', v_rationale),
    'fallback_level', v_fallback,
    'tone_modifiers', v_tone_mods,
    'visual_modifiers', v_visual_mods,
    'dishes', v_dishes,
    'recommended_sku', v_sku,
    'trend_hook', p_trend,
    'guardrails', COALESCE(v_guardrails, '[]'::jsonb),
    'applied_rules', v_applied,
    'warnings', v_warnings
  );
END $$;
