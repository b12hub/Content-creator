-- =====================================================================
-- BioLife Water (Imir Trade Group) — AI Ad Context Database
-- File 01: SCHEMA (PostgreSQL 14+)
-- Run order: 01_schema.sql -> 02_functions.sql -> 03_seed.sql
-- =====================================================================
BEGIN;

CREATE SCHEMA IF NOT EXISTS biolife;
SET search_path TO biolife, public;

-- ---------------------------------------------------------------------
-- ENUM TYPES (extend later with: ALTER TYPE ... ADD VALUE 'NEW_VALUE';)
-- ---------------------------------------------------------------------
CREATE TYPE framework_type AS ENUM (
  'COCA_COLA_EMOTIONAL',
  'PEPSI_BEHAVIORAL_DOPAMINE',
  'HYBRID_SPRING_RENEWAL'
);

CREATE TYPE caloric_density_type AS ENUM ('Extremely High', 'High', 'Moderate');

CREATE TYPE dish_category_type AS ENUM (
  'Heavy Rice', 'Grilled Meat', 'Baked Pastry', 'Soups/Stew',
  'Dough/Noodles',   -- added: lagmon, manti, norin do not fit the 4 original categories
  'Ritual Dish'      -- added: sumalak (Navruz)
);

CREATE TYPE sku_type AS ENUM ('0.33L Glass', '0.5L PET', '1.5L PET', '5L/10L Family Pack');

CREATE TYPE season_type AS ENUM ('Spring', 'Summer', 'Autumn', 'Winter');

-- How an event is placed on the calendar
CREATE TYPE anchor_type AS ENUM (
  'FIXED_DATE',       -- one Gregorian day window every year (Navruz, Sept 1)
  'GREGORIAN_RANGE',  -- recurring Gregorian season (Chilla, wedding season)
  'HIJRI_LUNAR'       -- floating; real dates live in event_occurrences
);

-- ---------------------------------------------------------------------
-- 1. cultural_events_seasons
-- ---------------------------------------------------------------------
CREATE TABLE cultural_events_seasons (
  id                  INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key                 TEXT        NOT NULL UNIQUE CHECK (key ~ '^[a-z0-9_]+$'),
  name_uz             TEXT        NOT NULL,
  name_ru             TEXT        NOT NULL,
  temporal_anchor     TEXT        NOT NULL,            -- human-readable, e.g. 'Lunar Month 9'
  anchor_type         anchor_type NOT NULL,
  -- machine-readable window for FIXED_DATE / GREGORIAN_RANGE (may wrap year: Dec 20 -> Jan 3)
  start_month         SMALLINT CHECK (start_month BETWEEN 1 AND 12),
  start_day           SMALLINT CHECK (start_day   BETWEEN 1 AND 31),
  end_month           SMALLINT CHECK (end_month   BETWEEN 1 AND 12),
  end_day             SMALLINT CHECK (end_day     BETWEEN 1 AND 31),
  -- informational Hijri anchor for HIJRI_LUNAR
  hijri_month         SMALLINT CHECK (hijri_month BETWEEN 1 AND 12),
  hijri_day_start     SMALLINT CHECK (hijri_day_start BETWEEN 1 AND 30),
  hijri_day_end       SMALLINT CHECK (hijri_day_end   BETWEEN 1 AND 30),
  priority            SMALLINT NOT NULL DEFAULT 10,   -- higher wins when events overlap
  physiological_state TEXT     NOT NULL,
  emotional_state     TEXT     NOT NULL,
  cultural_rituals    TEXT[]   NOT NULL DEFAULT '{}',
  semiotic_symbols    TEXT[]   NOT NULL DEFAULT '{}',
  content_guardrails  JSONB    NOT NULL DEFAULT '{}'::jsonb,  -- hard rules for the copywriter
  metadata            JSONB    NOT NULL DEFAULT '{}'::jsonb,
  is_active           BOOLEAN  NOT NULL DEFAULT TRUE,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT chk_gregorian_window CHECK (
    anchor_type = 'HIJRI_LUNAR'
    OR (start_month IS NOT NULL AND start_day IS NOT NULL
        AND end_month IS NOT NULL AND end_day IS NOT NULL)
  ),
  CONSTRAINT chk_hijri_anchor CHECK (anchor_type <> 'HIJRI_LUNAR' OR hijri_month IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- 1b. event_occurrences — concrete dates per year (required for lunar events,
--     optional override for anything else). Update yearly from muslim.uz.
-- ---------------------------------------------------------------------
CREATE TABLE event_occurrences (
  id            INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id      INT  NOT NULL,
  year          SMALLINT NOT NULL CHECK (year BETWEEN 2020 AND 2100),
  start_date    DATE NOT NULL,
  end_date      DATE NOT NULL,
  is_confirmed  BOOLEAN NOT NULL DEFAULT FALSE,   -- FALSE = astronomical estimate
  source        TEXT,
  UNIQUE (event_id, year),
  CHECK (end_date >= start_date)
);

-- ---------------------------------------------------------------------
-- 2. psychological_frameworks
-- ---------------------------------------------------------------------
CREATE TABLE psychological_frameworks (
  id                    INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  framework_name        framework_type NOT NULL UNIQUE,
  neural_target         TEXT NOT NULL,
  primary_trigger       TEXT NOT NULL,
  narrative_tone        TEXT NOT NULL,
  visual_style          TEXT NOT NULL,
  voiceover_guidelines  JSONB NOT NULL DEFAULT '{}'::jsonb,
  instagram_format      JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_default            BOOLEAN NOT NULL DEFAULT FALSE,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- exactly one global fallback framework
CREATE UNIQUE INDEX uq_one_default_framework ON psychological_frameworks (is_default) WHERE is_default;

-- ---------------------------------------------------------------------
-- 3. event_framework_mappings (junction)
-- ---------------------------------------------------------------------
CREATE TABLE event_framework_mappings (
  event_id            INT NOT NULL,
  framework_id        INT NOT NULL,
  selection_rationale TEXT NOT NULL,
  weight              NUMERIC(3,2) NOT NULL DEFAULT 1.00 CHECK (weight > 0 AND weight <= 1),
  PRIMARY KEY (event_id, framework_id)
);

-- ---------------------------------------------------------------------
-- 4. national_dishes_pairing
-- ---------------------------------------------------------------------
CREATE TABLE national_dishes_pairing (
  id                        INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key                       TEXT NOT NULL UNIQUE CHECK (key ~ '^[a-z0-9_]+$'),
  dish_name_uz              TEXT NOT NULL,
  dish_name_ru              TEXT NOT NULL,
  dish_category             dish_category_type   NOT NULL,
  caloric_density           caloric_density_type NOT NULL,
  serving_temperature       TEXT NOT NULL CHECK (serving_temperature IN ('HOT','WARM','COLD')),
  fat_and_spice_profile     TEXT NOT NULL,
  physiological_impact      TEXT NOT NULL,
  biolife_pairing_mechanics TEXT NOT NULL,
  recommended_sku           sku_type NOT NULL,
  alternative_skus          sku_type[] NOT NULL DEFAULT '{}',
  typical_occasions         TEXT[] NOT NULL DEFAULT '{}',
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------
-- 5. annual_calendar_grid (12 rows = 365-day baseline)
-- ---------------------------------------------------------------------
CREATE TABLE annual_calendar_grid (
  month_number              SMALLINT PRIMARY KEY CHECK (month_number BETWEEN 1 AND 12),
  season_name               season_type NOT NULL,
  average_temperature_range TEXT NOT NULL,             -- display text, e.g. '-3°C … +7°C'
  avg_temp_min_c            NUMERIC(4,1) NOT NULL,     -- machine-readable
  avg_temp_max_c            NUMERIC(4,1) NOT NULL,
  primary_cultural_context  TEXT NOT NULL,
  default_dish_pairings     INT[] NOT NULL DEFAULT '{}',   -- ids from national_dishes_pairing
  active_framework_id       INT NOT NULL,
  CHECK (avg_temp_max_c >= avg_temp_min_c)
);

-- ---------------------------------------------------------------------
-- 6. inference_rules — runtime overrides (temperature, trends, event guardrails)
--    condition keys supported by resolve_ad_context():
--      temp_gte, temp_lte, event_key_in (array), month_in (array), trend_present (bool)
--    action keys: framework (framework_type), sku (sku_type), tone_modifier, visual_modifier, note
-- ---------------------------------------------------------------------
CREATE TABLE inference_rules (
  id          INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  rule_key    TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL,
  priority    SMALLINT NOT NULL DEFAULT 10,   -- higher applied last (wins)
  condition   JSONB NOT NULL,
  action      JSONB NOT NULL,
  is_active   BOOLEAN NOT NULL DEFAULT TRUE,
  CHECK (jsonb_typeof(condition) = 'object' AND jsonb_typeof(action) = 'object')
);

-- ---------------------------------------------------------------------
-- FOREIGN KEYS (ALTER TABLE, per spec)
-- ---------------------------------------------------------------------
ALTER TABLE event_occurrences
  ADD CONSTRAINT fk_occ_event FOREIGN KEY (event_id)
  REFERENCES cultural_events_seasons(id) ON DELETE CASCADE;

ALTER TABLE event_framework_mappings
  ADD CONSTRAINT fk_efm_event FOREIGN KEY (event_id)
  REFERENCES cultural_events_seasons(id) ON DELETE CASCADE,
  ADD CONSTRAINT fk_efm_framework FOREIGN KEY (framework_id)
  REFERENCES psychological_frameworks(id) ON DELETE RESTRICT;

ALTER TABLE annual_calendar_grid
  ADD CONSTRAINT fk_grid_framework FOREIGN KEY (active_framework_id)
  REFERENCES psychological_frameworks(id) ON DELETE RESTRICT;

-- Arrays cannot carry FKs -> enforce dish ids with a trigger
CREATE OR REPLACE FUNCTION trg_check_grid_dishes() RETURNS trigger
LANGUAGE plpgsql
SET search_path = biolife, public
AS $$
DECLARE missing INT[];
BEGIN
  SELECT array_agg(d) INTO missing
  FROM unnest(NEW.default_dish_pairings) d
  WHERE NOT EXISTS (SELECT 1 FROM national_dishes_pairing n WHERE n.id = d);
  IF missing IS NOT NULL THEN
    RAISE EXCEPTION 'annual_calendar_grid.month %: unknown dish ids %', NEW.month_number, missing;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER grid_dishes_fk
  BEFORE INSERT OR UPDATE ON annual_calendar_grid
  FOR EACH ROW EXECUTE FUNCTION trg_check_grid_dishes();

-- Block deleting a dish that the calendar still references
CREATE OR REPLACE FUNCTION trg_protect_dish() RETURNS trigger
LANGUAGE plpgsql
SET search_path = biolife, public
AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM annual_calendar_grid WHERE OLD.id = ANY(default_dish_pairings)) THEN
    RAISE EXCEPTION 'dish % is referenced by annual_calendar_grid', OLD.key;
  END IF;
  RETURN OLD;
END $$;

CREATE TRIGGER dish_delete_guard
  BEFORE DELETE ON national_dishes_pairing
  FOR EACH ROW EXECUTE FUNCTION trg_protect_dish();

-- updated_at maintenance
CREATE OR REPLACE FUNCTION trg_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql
SET search_path = biolife, public
AS $$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

CREATE TRIGGER events_touch BEFORE UPDATE ON cultural_events_seasons
  FOR EACH ROW EXECUTE FUNCTION trg_touch_updated_at();

-- ---------------------------------------------------------------------
-- INDEXES
-- ---------------------------------------------------------------------
CREATE INDEX ix_occ_dates      ON event_occurrences (start_date, end_date);
CREATE INDEX ix_events_anchor  ON cultural_events_seasons (anchor_type) WHERE is_active;
CREATE INDEX ix_efm_framework  ON event_framework_mappings (framework_id);
CREATE INDEX ix_grid_dishes    ON annual_calendar_grid USING GIN (default_dish_pairings);
CREATE INDEX ix_rules_active   ON inference_rules (priority) WHERE is_active;

COMMIT;
