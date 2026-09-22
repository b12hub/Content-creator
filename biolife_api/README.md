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

## Telegram bot — internal AI copywriter (aiogram 3.x, webhook)
Internal tool for the BioLife marketing team. All bot copy is Uzbek (Latin); the generated script
itself is bilingual RU/UZ.

| File | Role |
|---|---|
| `app/telegram/handlers.py` | `/start`, `/create`, `/cancel`, free-form briefs, inline buttons, edit FSM |
| `app/telegram/services.py` | Real Anthropic calls + the three system prompts |
| `app/telegram/keyboards.py` | `btn_gen_video_prompt` / `btn_edit_script` |
| `app/telegram/states.py` | `ContentStates.waiting_for_script_edits` + FSM data keys |
| `app/telegram/bot.py` | Bot + Dispatcher (injects `llm` and `settings`), commands, access control |
| `app/routers/telegram.py` | FastAPI webhook receiver (unchanged) |

Flows:
- **Any text** → that text is the brief → script + inline keyboard.
- **/create** → today's default campaign (date, month, season in Tashkent time).
- **🎬 button** → the stored script becomes AI-video prompts (Runway / Luma / Sora / Midjourney),
  delivered in `<pre>` blocks for one-tap copy.
- **✏️ button** → state `waiting_for_script_edits` → the next message is feedback → revised script
  with the keyboard re-attached. `/cancel` or `/create` always leave edit mode.
- **Unknown command** (`/foo`) → a hint, never a paid LLM call.

### Models
The bot uses plain-text completions, so any model works, including `claude-3-5-sonnet-20240620`.
Pin one for the bot with `BIOLIFE_TELEGRAM_LLM_MODEL` — do **not** set `BIOLIFE_ANTHROPIC_MODEL`
to a 3.x model: `/generate-reel-script` and `/generate-campaign-package` use structured outputs,
which need Sonnet 4.5+ / Opus 4.5+ / Haiku 4.5 (verified in Anthropic's docs).

### Notes
- Set `BIOLIFE_TELEGRAM_ALLOWED_USER_IDS`, otherwise anyone can spend your Anthropic credit.
- FSM is in-memory: the active script lives in the process. Run a single uvicorn worker, or move
  the storage to Redis before scaling out.
- One generation per user at a time; every long answer is split so no HTML tag or entity is cut,
  and a formatting rejection falls back to plain text instead of losing the answer.

## Hybrid LLM: Anthropic → OpenRouter fallback
`app/llm/fallback.py` wraps the primary client. `app/telegram/services.generate_with_fallback()`
is the single entry point every bot generation goes through (`/create`, free-form briefs, the 🎬
video prompt and the ✏️ edit flow all use it).

```
Anthropic  --ok-->  answer
   |  auth error / 429 / 529 / 5xx / timeout / connection error / missing key
   v
OpenRouter (httpx, OpenAI-compatible)  -->  answer + "⚠️ zaxira model" notice in the chat
```

Enable it:
```bash
BIOLIFE_OPENROUTER_API_KEY=sk-or-...
BIOLIFE_OPENROUTER_FALLBACK_MODEL=nvidia/nemotron-3-ultra:free
```
Without the key the bot behaves exactly as before (no wrapper, original errors).

### Deliberate limits
- **A refusal never falls back.** Refusals are content decisions, not outages; routing them to
  another provider would be a way around the safety decision.
- **`generate_structured()` never falls back.** `/generate-reel-script` and
  `/generate-campaign-package` depend on Anthropic's structured outputs; a free model gives no
  schema guarantee, so failing loudly beats returning JSON that breaks the contract.
- **Time budget:** one primary attempt is capped at `BIOLIFE_LLM_PRIMARY_TIMEOUT_S` (45 s) and the
  fallback at `BIOLIFE_OPENROUTER_TIMEOUT_S` (60 s), so both fit inside the handler's 120 s. The
  app warns at startup if the sum leaves no room.
- **Degraded answers are labelled** in the chat: the free model's Uzbek is weaker and a marketer
  must know which model wrote the text.

### Verified against OpenRouter's docs (Sep 2026)
- `POST https://openrouter.ai/api/v1/chat/completions`; `HTTP-Referer` + `X-Title` for attribution.
- Errors arrive as HTTP status codes with `{"error": {...}}`; the client also guards against a
  200 that carries an error object, an empty answer, and `content` sent as a list of parts.
- `:free` models allow **20 requests/minute and 50/day** (1000 after buying ≥10 credits), so the
  fallback is a safety net, not a second primary.
- **The default slug `nvidia/nemotron-3-ultra:free` is not in OpenRouter's catalogue today.**
  Pick a current id from `https://openrouter.ai/api/v1/models`; the app checks the id at startup
  in the background and logs an error if it is unknown.
