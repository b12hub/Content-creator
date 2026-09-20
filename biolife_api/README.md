# BioLife AI Campaign Engine — Phases 2-4 (FastAPI)

Takes the JSON from `biolife.resolve_ad_context()` (Phase 1) and returns a bilingual (RU + UZ Latin)
Instagram Reel script in 4 parts: `strategic_selection`, `visual_hook`, `script`, `call_to_action`.

## Pipeline
```
POST /generate-reel-script  (body = DB JSON)
  → Pydantic validation (AdContext)                       422 if the payload is broken
  → factory: framework.name → exactly ONE handler          deterministic, no LLM routing
       COCA_COLA_EMOTIONAL        → EmotionalHandler  "USTOZ"  (hippocampus / tradition)
       PEPSI_BEHAVIORAL_DOPAMINE  → DopamineHandler   "ZARBA"  (cue → action → reward)
       HYBRID_SPRING_RENEWAL      → HybridHandler     "BAHOR"  (renewal + tradition)
  → system prompt = route persona + universal contract + DB guardrails
  → LLM with structured JSON output (Anthropic or OpenAI)
  → deterministic QA (language, brand safety, route boundaries, lengths, CTA)
       fail → 1 repair attempt with the rejected draft + issue list → still fails → 502
  → response + meta + quality {review_required, review_reasons}
```

## Run
```bash
pip install -r requirements.txt
cp .env.example .env            # add your API key
uvicorn app.main:app --reload   # http://127.0.0.1:8000/docs
BIOLIFE_LLM_PROVIDER=fake uvicorn app.main:app   # offline demo, no key
python3 -m pytest -q            # 76 tests
```
Example:
```bash
curl -X POST localhost:8000/generate-reel-script -H 'content-type: application/json' \
     --data @tests/fixtures/chilla_43C.json
```
Get the body straight from Postgres:
`SELECT biolife.resolve_ad_context('2027-07-15', 43, 'osh', NULL);`

## Where things are
| File | What |
|---|---|
| `app/handlers/emotional.py`, `dopamine.py`, `hybrid.py` | The 3 route personas / system prompts |
| `app/handlers/prompt_blocks.py` | Shared output contract, guardrails, brief rendering |
| `app/services/quality.py` | Automatic QA rules |
| `app/config.py` | Model, provider, **CTA channels (placeholders!)** |
| `docs/rendered_prompts/` | Full prompts exactly as sent to the LLM for 4 real DB cases |

## Adding a 4th framework
1. `ALTER TYPE biolife.framework_type ADD VALUE 'X'` in the DB.
2. Add `X` to `FrameworkName` in `app/models/context.py`.
3. Create `app/handlers/x.py` (subclass `BaseReelHandler`, write `persona_prompt`) and register it in `factory.py`.
   The app refuses to start if a framework has no handler.

## Phase 3-4: `POST /generate-campaign-package`
Body = the same `resolve_ad_context()` JSON. Optional query `?product_launch=true`.
Returns one `CampaignPackage`: 4-part creative (CTA handle/link injected from config), QA report,
Meta Ads media plan, `ready_to_launch` + `blockers`. Samples: `docs/sample_packages/`.

Order: media plan first (instant, no cost) → LLM script → merge. Bad payload = 422 before any LLM call.

### Budget tiers (`app/services/media_planner.py`, rules are deterministic)
| Tier | UZS / month | Reels | Meta objective → optimization | Triggers |
|---|---|---|---|---|
| STANDARD | 1,500,000 | 4 | OUTCOME_AWARENESS → THRUPLAY | default |
| DRIVE | 3,000,000 | 8-10 | OUTCOME_TRAFFIC → LINK_CLICKS | temp ≥ 38°C, Ramadan, event priority ≥ 85 |
| PREMIUM | 8,000,000 | 10+ | OUTCOME_SALES → OFFSITE_CONVERSIONS* | Navruz, last 10 days of Ramadan, product launch |

\* Falls back to LINK_CLICKS (and blocks launch) until a Pixel / Conversions API dataset is live.
Remembrance day (May 9) is always capped to STANDARD awareness.
Event-driven tiers end with the event (e.g. Ramadan); weather/launch tiers run 30 days.

### Meta rules baked in (checked in Meta developer docs, Sep 2026)
- UZS is **not** a Meta ad-account currency → budgets are output in USD (`BIOLIFE_UZS_PER_USD`, cbu.uz rate).
- Audience Network cannot run alone and **requires Facebook** placements → Facebook Reels is added
  (`BIOLIFE_INCLUDE_AUDIENCE_NETWORK=false` = Instagram Reels only).
- Cities use `custom_locations` (lat/long + radius 1-80 km) — no city keys needed.
- Interests are returned as search terms: resolve IDs with `GET /search?type=adinterest&q=...`.
- Ramadan ad sets use `adset_schedule` (19:00-05:00, after iftar) + `pacing_type: day_parting` + lifetime budget.
- No religion-based interest targeting.

DB note: the planner uses `primary_event.priority/day_index/days_total/window_*`, added to
`resolve_ad_context()` in biolife_db v1.1 (additive; older payloads still validate).
