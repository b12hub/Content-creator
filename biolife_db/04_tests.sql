-- File 04: AUTOMATED TESTS. Run after 01-03. Any failure raises an exception.
SET search_path TO biolife, public;
\set ON_ERROR_STOP on

CREATE OR REPLACE FUNCTION pg_temp.check(label TEXT, ok BOOLEAN) RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
  IF ok IS NOT TRUE THEN RAISE EXCEPTION 'FAIL: %', label; END IF;
  RAISE NOTICE 'PASS: %', label;
END $$;

-- 1. 365/366-day coverage: every day of 2026-2028 resolves a framework, an event and >=1 dish
SELECT pg_temp.check('every day 2026-2028 has framework + event + dish',
  NOT EXISTS (
    SELECT 1 FROM generate_series('2026-01-01'::date, '2028-12-31', '1 day') g(d),
         LATERAL (SELECT resolve_ad_context(g.d::date) r) x
    WHERE x.r->'framework'->>'name' IS NULL
       OR x.r->'primary_event' IS NULL
       OR jsonb_array_length(x.r->'dishes') = 0));

-- 2. Chilla + live heat
WITH r AS (SELECT resolve_ad_context('2027-07-15', 43, NULL, NULL) j)
SELECT pg_temp.check('Chilla 43C -> chilla_summer + PEPSI + 0.5L',
  j->'primary_event'->>'key' = 'chilla_summer'
  AND j->'framework'->>'name' = 'PEPSI_BEHAVIORAL_DOPAMINE'
  AND j->>'recommended_sku' = '0.5L PET') FROM r;

-- 3. Ramadan beats heat rule, guardrails and estimate warning present
WITH r AS (SELECT resolve_ad_context('2027-02-20', 36, NULL, NULL) j)
SELECT pg_temp.check('Ramadan + 36C -> COCA (religious override), guardrails, warning',
  j->'primary_event'->>'key' = 'ramadan'
  AND j->'framework'->>'name' = 'COCA_COLA_EMOTIONAL'
  AND j->'applied_rules' ? 'religious_guardrail'
  AND j::text LIKE '%Never depict eating or drinking during daylight%'
  AND jsonb_array_length(j->'warnings') >= 1) FROM r;

-- 4. Navruz
WITH r AS (SELECT resolve_ad_context('2027-03-21') j)
SELECT pg_temp.check('Navruz -> HYBRID + sumalak first',
  j->'primary_event'->>'key' = 'navruz'
  AND j->'framework'->>'name' = 'HYBRID_SPRING_RENEWAL'
  AND j->'dishes'->0->>'key' = 'sumalak') FROM r;

-- 5-6. New Year window wraps the year
SELECT pg_temp.check('Dec 31 -> new_year', resolve_ad_context('2026-12-31')->'primary_event'->>'key' = 'new_year');
SELECT pg_temp.check('Jan 1 -> new_year (year wrap)', resolve_ad_context('2027-01-01')->'primary_event'->>'key' = 'new_year');

-- 7. Unknown dish -> warning + fallback dishes
WITH r AS (SELECT resolve_ad_context('2027-10-10', 20, 'pizza', NULL) j)
SELECT pg_temp.check('Unknown dish -> warning + fallback',
  j->'warnings'->>0 LIKE 'Unknown dish_key%' AND jsonb_array_length(j->'dishes') > 0) FROM r;

-- 8. Memorial day low-key
WITH r AS (SELECT resolve_ad_context('2027-05-09', 30, NULL, NULL) j)
SELECT pg_temp.check('May 9 -> memorial_day + COCA + memorial rule',
  j->'primary_event'->>'key' = 'memorial_day'
  AND j->'framework'->>'name' = 'COCA_COLA_EMOTIONAL'
  AND j->'applied_rules' ? 'memorial_low_key') FROM r;

-- 9. Cold weather rule
SELECT pg_temp.check('-2C -> cold_weather rule',
  resolve_ad_context('2027-01-10', -2, NULL, NULL)->'applied_rules' ? 'cold_weather');

-- 10. Kurban Hayit in heat stays emotional
SELECT pg_temp.check('Kurban Hayit + 36C -> COCA',
  resolve_ad_context('2027-05-17', 36, NULL, NULL)->'framework'->>'name' = 'COCA_COLA_EMOTIONAL');

-- 11. Trend input
SELECT pg_temp.check('Trend supplied -> trend_hook rule',
  resolve_ad_context('2027-08-10', NULL, NULL, 'ice challenge')->'applied_rules' ? 'trend_hook');

-- 12. User-selected dish drives SKU
WITH r AS (SELECT resolve_ad_context('2027-10-15', 20, 'norin', NULL) j)
SELECT pg_temp.check('norin -> 0.33L Glass',
  j->'dishes'->0->>'key' = 'norin' AND j->>'recommended_sku' = '0.33L Glass') FROM r;

-- 13. Leap day covered by winter season
SELECT pg_temp.check('2028-02-29 includes winter_cold_season',
  resolve_ad_context('2028-02-29')->'all_active_events' ? 'winter_cold_season');

-- 14. No temp input -> monthly average used
SELECT pg_temp.check('No temp -> monthly_average_max',
  resolve_ad_context('2027-07-20')->>'temperature_source' = 'monthly_average_max');

-- 15. Fallback chain (inside a rolled-back transaction)
BEGIN;
UPDATE cultural_events_seasons SET is_active = FALSE;
SELECT pg_temp.check('No events -> MONTH_GRID fallback',
  resolve_ad_context('2027-04-10', 20, NULL, NULL)->>'fallback_level' = 'MONTH_GRID');
DELETE FROM annual_calendar_grid WHERE month_number = 4;
SELECT pg_temp.check('No events + no grid -> GLOBAL_DEFAULT (HYBRID)',
  resolve_ad_context('2027-04-10', 20, NULL, NULL)->>'fallback_level' = 'GLOBAL_DEFAULT'
  AND resolve_ad_context('2027-04-10', 20, NULL, NULL)->'framework'->>'name' = 'HYBRID_SPRING_RENEWAL');
ROLLBACK;

-- 16. Integrity guards
DO $$ BEGIN
  BEGIN
    UPDATE annual_calendar_grid SET default_dish_pairings = '{9999}' WHERE month_number = 1;
    RAISE EXCEPTION 'FAIL: bad dish id accepted';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'FAIL%' THEN RAISE; END IF;
    RAISE NOTICE 'PASS: grid rejects unknown dish id';
  END;
  BEGIN
    DELETE FROM national_dishes_pairing WHERE key = 'osh';
    RAISE EXCEPTION 'FAIL: referenced dish deleted';
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM LIKE 'FAIL%' THEN RAISE; END IF;
    RAISE NOTICE 'PASS: referenced dish cannot be deleted';
  END;
  BEGIN
    UPDATE psychological_frameworks SET is_default = TRUE;
    RAISE EXCEPTION 'FAIL: two defaults allowed';
  EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'PASS: only one default framework';
  END;
END $$;

-- Row counts
SELECT 'frameworks' t, count(*) FROM psychological_frameworks UNION ALL
SELECT 'events', count(*) FROM cultural_events_seasons UNION ALL
SELECT 'occurrences', count(*) FROM event_occurrences UNION ALL
SELECT 'mappings', count(*) FROM event_framework_mappings UNION ALL
SELECT 'dishes', count(*) FROM national_dishes_pairing UNION ALL
SELECT 'grid_months', count(*) FROM annual_calendar_grid UNION ALL
SELECT 'rules', count(*) FROM inference_rules;

-- 17. Event window / day index (added for the media planner, Phase 4)
WITH r AS (SELECT resolve_ad_context('2027-03-01')->'primary_event' j)
SELECT pg_temp.check('Ramadan 2027-03-01 -> day 22 of 29, priority 100',
  j->>'key' = 'ramadan' AND (j->>'day_index')::int = 22 AND (j->>'days_total')::int = 29
  AND (j->>'priority')::int = 100) FROM r;
SELECT pg_temp.check('New Year wrap window Jan 2 -> started previous Dec 20',
  resolve_ad_context('2027-01-02')->'primary_event'->>'window_start' = '2026-12-20');
SELECT pg_temp.check('Winter window in non-leap year clamps Feb 29 -> Feb 28',
  (SELECT window_end FROM event_window((SELECT id FROM cultural_events_seasons WHERE key='winter_cold_season'), '2027-01-10')) = '2027-02-28');
SELECT pg_temp.check('Backward compatible: old keys still present',
  resolve_ad_context('2027-07-15', 43, 'osh', NULL) ?& ARRAY['framework','dishes','recommended_sku','guardrails','primary_event','warnings']);
