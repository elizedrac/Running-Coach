# RunCoach Agent — Design Document

---

## Table of Contents

1. [Overview](#overview)
2. [Tech Stack](#tech-stack)
3. [File Structure](#file-structure)
4. [Database Schema](#database-schema)
5. [Architecture & Data Flow](#architecture--data-flow)
6. [Entry Points & Modularity](#entry-points--modularity)
7. [LLM Strategy](#llm-strategy)
8. [Tool Suite](#tool-suite)
9. [LLM Flow](#llm-flow)
10. [Caching Strategy](#caching-strategy)
11. [ML Model](#ml-model)
12. [Guardrails & Validation](#guardrails--validation)
13. [Memory & Compression](#memory--compression)
14. [Error Handling & Retry Logic](#error-handling--retry-logic)
15. [External APIs](#external-apis)
16. [GitHub Actions](#github-actions)
17. [Implementation Order](#implementation-order)
18. [V2 Scope](#v2-scope)

---

## Overview

A personal AI running coach that ingests Garmin health and activity data, stores it in Supabase, and exposes an LLM-powered tool system for training plan creation, race readiness analysis, and performance insights. Built in Python with FastAPI. Designed for solo use initially with a clear path to multi-user deployment.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Language | Python | Primary language throughout |
| Framework | FastAPI | Backend API + webhook handler |
| Database | Supabase (PostgreSQL) | All persistent storage |
| LLM | Anthropic Claude | Sonnet for main calls, Haiku for lightweight |
| ML | XGBoost | Race time prediction |
| In-memory cache | cachetools TTLCache | Per-session, no persistence |
| Auth | Supabase Auth | Email/password login; JWT validated server-side via `get_user()` |
| Hosting (app) | AWS EC2 | Docker + nginx reverse proxy |
| Hosting (DB) | Supabase cloud | Already cloud-hosted, no migration needed |
| Weather | WeatherAPI.com | Free tier, API key required, current day + 12-hour forecast |
| Web search | Anthropic web search tool | Built-in, no extra API key |
| Race results | Athlinks via web search | Event ID extracted from search results |
| Package manager | uv | `uv sync` installs deps; `uv run` executes in venv |
| Linter | ruff | Lint + format via `make lint` / `make fix` |
| CI/CD | GitHub Actions | Daily Garmin sync cron |

---

## File Structure

```
runcoach/
│
├── cli.py                         # CLI entry point (V1) — thin wrapper, calls services/coach.py::ask()
├── main.py                        # FastAPI app entry point (Phase 4) — registers routes/
├── conftest.py                    # pytest fixtures (Supabase client mock)
├── pyproject.toml                 # project deps + ruff + pytest config
├── uv.lock                        # locked dependency versions
├── Makefile                       # dev workflow shortcuts (sync, test, lint, fix, run, build, up, down, logs)
├── .env                           # API keys, Supabase URL/key, hardcoded user_id for V1
├── requirements.txt               # generated from uv.lock via make sync-requirements; used by Docker
├── DESIGN.md
│
├── static/
│   ├── index.html                 # Single-file frontend (served by FastAPI via StaticFiles); checks sessionStorage for JWT on load, redirects to login if missing
│   └── login.html                 # Login page — POST /auth/login, stores JWT in sessionStorage, redirects to /
│
├── routes/
│   ├── ask.py                     # POST /ask — SSE streaming endpoint; owns per-session History dict
│   ├── activities.py              # GET /health/recent, /health/v02, /health/body-battery, /activities/recent, /weather; POST /garmin-sync
│   ├── plan.py                    # Plan CRUD: GET /plan/days, GET /plan/intervals/{day_id}, POST /plan/create, POST /plan/sync, DELETE /plan/delete, PATCH /plan/day/{day_id}, DELETE /plan/day/{day_id}
│   ├── auth.py                    # POST /auth/login — Supabase Auth sign-in, returns JWT access token
│   └── user.py                    # GET /user/info, POST /user/info/name, /user/info/theme, /user/info/location — profile read/write, scoped via get_current_user
│
├── services/
│   ├── coach.py                   # Orchestrator — generator that yields SSE events (status, chunk, done)
│   ├── llm.py                     # call_llm() (blocking) + stream_llm() (generator) with retry + prompt caching
│   ├── memory.py                  # compress_history() — Haiku call that summarises conversation to plain text
│   ├── end.py                     # detect_end() + generate_followups() — end-of-conversation detection and follow-up chips
│   ├── planner.py                 # Planner LLM call + PlannerOutput validation
│   ├── sql_selector.py            # Haiku call that picks query functions from REGISTRY; called internally by query_data tool
│   ├── trend_analysis.py          # 14 per-metric trend functions + compute_body_battery (3-day weighted) + compute_load
│   ├── final.py                   # Final LLM call (Sonnet) — generator, yields chunks via stream_llm
│   ├── garmin.py                  # Garmin data sync (token-cached auth, upsert to Supabase)
│   ├── plan.py                    # Training plan creation (agentic Opus loop + guardrails), update (intent-based ±7d window), sync
│   ├── guardrails.py              # challenger() — validates plan against hard rules before save; input_check() — blocks short/long queries
│   ├── web_search.py              # Anthropic web search wrapper — no persistence itself; caching is owned by race_info.py
│   ├── race_info.py               # get_race_info() — time-sensitive race logistics (registration/race_day); fuzzy cache lookup (word overlap → Voyage embedding cosine sim) via db/search_cache.py before falling back to web_search + LLM
│   ├── weather.py                 # WeatherAPI wrapper (current day + 12-hour forecast)
│   ├── cache.py                   # TTLCache singleton + range-aware cache logic (get_cached, set_cached)
│   ├── auth.py                    # get_current_user() FastAPI dependency — validates Bearer JWT via Supabase, returns user_id
│   ├── pacing.py                  # pacing_calculator() — Riegel equivalent marathon pace → Daniels-style zones + GPS-adjusted pace + VO2-derived easy pace
│   ├── course_details.py          # get_course_details() — RAG over course_chunks.json (word overlap + Voyage embedding similarity) + web search fallback
│   └── prompts.py                 # All prompt strings: BASE_COACH, build_planner_system(), SQL_SELECTOR_SYSTEM, TOOL_SNIPPETS, TOOL_METADATA, UPDATE_PLAN_SYSTEM, CREATE_PLAN_SYSTEM, PLAN_CHECKER_SYSTEM. Single source of truth.
│
├── services/ml/
│   ├── features.py                # Feature extraction from Supabase
│   ├── train.py                   # Run manually / on retrain trigger
│   ├── predict.py                 # Runtime inference (model loaded at startup)
│   ├── retrain.py                 # Checks threshold + hot reloads model
│   └── evaluate.py                # MAE tracking, error over time
│
├── db/
│   ├── client.py                  # create_client() — imported everywhere
│   ├── queries.py                 # REGISTRY of callable SQL functions (name → callable + description + args); Haiku selects from this list
│   ├── activity_history.py        # insert_activities, get_activities (self-caching via services/cache.py)
│   ├── health_history.py          # insert_health_history, get_health_history (self-caching via services/cache.py)
│   ├── garmin.py                  # Garmin credential CRUD: get/save/delete credentials + save_garmin_token
│   ├── race.py                    # Read/write for the user's target race
│   ├── preferences.py             # Read/write for training_preferences; set_notes() saves athlete notes field
│   ├── plan.py                    # Plan read/write queries (current_plan, plan_days, plan_intervals, plan_history)
│   ├── user_info.py               # Read/write for user_info (name, theme, location, last_synced)
│   └── search_cache.py            # get_candidates()/set_cached() for race_info — partitioned by (topic, race, location, info_type), embedding stored as jsonb
│
├── models/
│   ├── planner.py                 # Pydantic models: PlannerOutput, SQLPlan, AskRequest, DataRequest, History (dataclass), EndBehaviorClassification, UpdatePlanOutput, PatchDayRequest, CourseDetailsPlan, RaceRequest, PreferencesRequest, RaceInfoPlan, RaceRegistrationInfo, RaceDayInfo, SyncPlanRequest, PlanDay, PlanInterval, PlanChange, LoginRequest, GarminCredentials, UserInfoRequest
│   └── finish_time_predictor.json # Serialised XGBoost model (V2)
│
├── knowledge/
│   ├── health_metrics.json        # Per-metric descriptions, typical ranges, interpretation (injected into final prompt when health data present)
│   ├── race_distances.json        # Standard distances in miles — used by pacing_calculator for race_type autofill
│   ├── race_miles.json            # Race distance → miles mapping for pacing lookups
│   ├── race_prep.md               # Race day knowledge base (nutrition, warm-up, pacing strategy, gear) — loaded into final.py and injected when race_prep_info tool runs
│   └── course_chunks.json         # RAG store for race course details — embeddings inline, keyed by location + race
│
├── tests/
│   ├── test_deterministic.py      # All deterministic logic (plan constraints etc)
│   ├── test_integration.py        # End-to-end consistency + temperature tuning
│   └── test_race_info.py          # get_candidates/set_cached, word-overlap + embedding similarity, get_race_info cache hit/miss paths
│
└── .github/
    └── workflows/
        ├── garmin_sync.yml           # Daily Garmin data sync cron
        └── weekly_plan_refresh.yml   # Monday 8am UTC — runs services/plan.py __main__ to refresh current week
```

---

## Database Schema

### race (user's target race — one per user)
```sql
create table race (
    id                  uuid default gen_random_uuid() primary key,
    user_id             uuid references auth.users(id) on delete cascade unique,
    race_type           text,
    goal_time           text,
    race_distance_miles integer,
    race_date           timestamptz,
    created_at          timestamptz default now()
);
```

### training_preferences (one per user)
```sql
create table training_preferences (
    id              uuid default gen_random_uuid() primary key,
    user_id         uuid references auth.users(id) on delete cascade unique,
    days_per_week   integer default 4,
    preferred_days  text[],                                     -- e.g. {'MON','WED','FRI','SAT'}
    avg_miles       float,
    max_miles       float,
    time_based      boolean default false,                      -- false = mile based, true = time based
    notes           text                                        -- athlete notes: injury history, constraints, preferences (set via set_notes tool)
);
```

### garmin_credentials (one per user)
```sql
create table garmin_credentials (
    id          uuid default gen_random_uuid() primary key,
    user_id     uuid references auth.users(id) on delete cascade unique,
    email       text,
    password    text,                                          -- plaintext; known gap, candidate for Supabase Vault
    token_json  text,                                           -- cached session tokens to avoid repeated logins
    updated_at  timestamptz default now()
);
```

### activity_history (per-activity rows from Garmin)
```sql
create table activity_history (
    id                  uuid default gen_random_uuid() primary key,
    user_id             uuid references auth.users(id) on delete cascade,
    garmin_activity_id  bigint unique,
    calendar_date       date,
    calories_burned     float,
    activity_type       text,
    miles               float,
    avg_hr              float,
    max_hr              float,
    total_time          interval,
    average_pace        text,
    created_at          timestamptz default now()
);
```

### activity_splits (lap/interval data per activity — V2)
```sql
create table activity_splits (
    id                  uuid default gen_random_uuid() primary key,
    garmin_activity_id  bigint references activity_history(garmin_activity_id) on delete cascade,
    split_num           integer,
    split_type          text,                -- LAP | INTERVAL | WARMUP | COOLDOWN
    distance_meters     float,
    total_time          interval,
    avg_pace            text,
    avg_hr              float,
    max_hr              float,
    created_at          timestamptz default now()
);
```

### health_history (one row per day; merges daily + sleep metrics)
```sql
create table health_history (
    id              uuid default gen_random_uuid() primary key,
    user_id         uuid references users(id),
    calendar_date   date,
    stress          integer,
    active_minutes  float,
    total_steps     integer,
    sleep_score     integer,
    total_sleep     interval,
    rhr             integer,
    total_kcal      integer,
    vo2_max         integer,
    hrv             integer,
    created_at      timestamptz default now()
);
```

### current_plan (active training plan — one per user)
```sql
create table current_plan (
    id              uuid default gen_random_uuid() primary key,
    user_id         uuid references auth.users(id) on delete cascade unique,               -- enforces one per user
    race_name       text,
    race_date       date,
    goal_time       interval,
    total_weeks     integer,
    created_at      timestamptz default now(),
    updated_at      timestamptz default now()
);
```

### plan_history (archived past plans)
```sql
create table plan_history (
    id              uuid default gen_random_uuid() primary key,
    user_id         uuid references users(id),
    race_name       text,
    race_date       date,
    goal_time       interval,
    created_at      timestamptz,
    archived_at     timestamptz default now(),
    plan_data       jsonb                                           -- full snapshot at archive time
);
```

### plan_days (one row per day of the active plan)
```sql
create table plan_days (
    id              uuid default gen_random_uuid() primary key,
    plan_id         uuid references current_plan(id) on delete cascade,
    plan_date       date,
    week_number     integer,
    day_of_week     text,                                           -- MON | TUE | WED | etc
    workout_type    text,                                           -- EASY | AEROBIC | LONG | TEMPO | INTERVAL | STRENGTH | REST | CROSS
    target_miles    float,
    target_pace     text,                                           -- e.g. "8:30-8:45/mi"
    notes           text,
    completed       boolean default false,
    completed_at    timestamptz
);
```

### plan_intervals (intervals within a specific plan day)
```sql
create table plan_intervals (
    id              uuid default gen_random_uuid() primary key,
    day_id          uuid references plan_days(id) on delete cascade,
    interval_num    integer,
    interval_type   text,                                           -- WARMUP | WORK | REST | COOLDOWN
    distance        text,                                           -- e.g. "1mi" or "400m"
    target_pace     text,
    duration        text,                                           -- alternative to distance e.g. "10min"
    rest_duration   text,                                           -- e.g. "90sec jog"
    notes           text
);
```

### model_predictions (XGBoost prediction tracking for retraining)
```sql
create table model_predictions (
    id              uuid default gen_random_uuid() primary key,
    user_id         uuid references users(id),
    predicted_time  integer,                                        -- seconds
    actual_time     integer,                                        -- filled in post-race
    features        jsonb,                                          -- snapshot of inputs at prediction time
    model_version   text,
    riegel_time     integer,                                        -- Riegel baseline for comparison
    created_at      timestamptz default now()
);
```

### search_cache (persistent cache for web search + external API results)
```sql
create table search_cache (
    id              uuid default gen_random_uuid() primary key,
    user_id         uuid references auth.users(id) on delete cascade,    -- nullable; not currently used by any query
    query           text,
    result          text,
    topic           text,                                           -- race_info
    race            text,
    location        text,
    info_type       text,                                           -- registration | race_day
    embedding       jsonb,                                          -- Voyage embedding, for fuzzy cache matching
    source          text,                                           -- web_search | open_meteo | athlinks
    expires_at      timestamptz,
    created_at      timestamptz default now()
);
-- indexes: (topic, race, location, info_type, expires_at)
```

### user_info (one per user)
```sql
create table user_info (
    id              uuid default gen_random_uuid() primary key,
    user_id         uuid references auth.users(id) on delete cascade unique,
    first_name      text,
    last_name       text,
    theme           text,
    location        text,
    last_synced     timestamptz,                                    -- stamped by services/garmin.py on successful sync
    created_at      timestamptz default now()
);
```

---

## Architecture & Data Flow

```
Garmin Device
└── syncs to Garmin Connect
    └── pings webhook (POST to /activities)
        └── FastAPI handler (routes/activities.py)
            └── Enrich with weather (services/weather.py)
                └── Insert to Supabase (db/activity_history.py)

User Question
└── CLI / POST /ask
    └── Planner LLM (Sonnet) — decides path + tools
        ├── no_tools → final LLM (2 calls)
        └── tools    → tool execution in declared order → final LLM (2-3 calls)
                       query_data tool calls Haiku internally to pick SQL func → self-caching DB query
```

---

## Entry Points & Modularity

V1 ships as a CLI. The FastAPI server is added in Phase 4. To avoid rewriting logic when the server lands, both entry points are thin wrappers over the same `services/` layer.

```
CLI (cli.py)    ─┐
                 ├──→ services/coach.py::ask(question, user_id) ──→ tools / db / llm
FastAPI route   ─┘
```

### Rules

1. **No business logic in `cli.py` or `routes/`.** They handle I/O only (parse arg / parse request) and call a service function.
2. **Services return data, not formatted output.** `ask()` returns a string or dict. The CLI prints it; the route serialises it to JSON. No `print()` inside services.
3. **Pass `user_id` explicitly.** Every service function takes `user_id` as a parameter, even in solo V1 (hardcoded in CLI from `.env`). This makes multi-user a config change, not a refactor.
4. **Config via env vars, not CLI flags.** Supabase keys, Anthropic key, model names all come from `.env`. Both entry points read the same config.
5. **No framework objects in services.** Never pass FastAPI's `Request` (or any web framework primitive) into a service function. Pass primitives only.

Result: switching from `python cli.py "how was my last run"` to `POST /ask {"question": "..."}` is a small route file, no service changes.

---

## LLM Strategy

### Model Allocation

| Call | Model | Reason |
|---|---|---|
| Planner (path + tools + timeframes) | Sonnet | High-stakes routing decision. Runs on every question |
| SQL function selection | Haiku | Picks which pre-built query to call, not generating SQL |
| Per-tool internal summarisation (web search, course details, etc.) | Haiku | Some tools call Haiku internally to condense their own output. Per-tool, optional, never a global step |
| Final coaching response | Sonnet | Main user-facing output. Per-tool snippets from `prompt_snippets.py` are appended to its system prompt based on which tools ran |
| Memory compression | Haiku | Lightweight summarisation |
| Follow-up generation | Haiku | Simple continuation logic |
| Race time prediction context | Sonnet | Analytical, user-facing |

### End-to-End Flow

```
User Input
→ Planner LLM (Sonnet) — decides path + selects tools/timeframes if needed
→ Branch:
    • no tools     → straight to final LLM
    • tools needed → tool execution (deterministic; some tools call sql_selector internally) → final LLM
→ Final LLM (Sonnet) — coaching response. System prompt = BASE + per-tool snippets for tools that ran.
→ End behaviour + follow-up logic
→ Persist to DB
```

### Prompt Architecture

**Central call wrapper** — all LLM calls go through one function:

```python
# services/llm.py
def call_llm(system_prompt: str, user_prompt: str, model: str = DEFAULT_MODEL,
             max_tokens: int = 1024, cache_system: bool = False) -> str
```

**Single prompts file** — all prompt strings live in `services/prompts.py`. Per-tool snippets are appended to the final LLM's system prompt based on which tools ran:

```python
# services/prompts.py
BASE_COACH = "You are an experienced running coach..."

PLANNER_SYSTEM = """..."""           # built dynamically from REGISTRY at startup
SQL_SELECTOR_SYSTEM = """..."""

TOOL_SNIPPETS = {
    "get_recent_runs":     "When analysing runs: compare target vs actual pace...",
    "get_sleep_for_date":  "When analysing sleep: flag deep sleep < 80min...",
    "query_database":      "When presenting queried data: summarise trend first...",
    "get_course_details":  "When discussing course: reference elevation and aid stations...",
    "predict_finish_time": "When presenting predictions: state confidence level...",
}

WEB_SEARCH_SUMMARY = "..."           # per-tool Haiku prompt
COURSE_DETAILS_SUMMARY = "..."       # per-tool Haiku prompt
COMPRESSION = "..."                  # memory compression
FOLLOW_UP = "..."                    # follow-up question generation
```

**Prompt caching** — implemented via `cache_system=True` flag on `call_llm()`. Enabled on all static system prompts: `BASE_COACH` (final LLM), `SQL_SELECTOR_SYSTEM` (Haiku), and the course details extraction prompt. The planner system prompt is dynamic (injects today's date) and is not cached. Minimum 1024 tokens for cache eligibility.

### Routing Logic

```
Question arrives
└── Planner (Sonnet) — every question goes through the planner
    ├── No tools needed
    │   → direct to final LLM
    │
    └── Tools needed
        → execute tools in declared order (deterministic)
        → some tools (get_plan, trend_analysis, query_user_data) call sql_selector internally
        → final LLM
```

---

## Tool Suite

### 1. Garmin Sync
- Triggered by webhook on device sync
- Always first if multiple tool calls
- Uses cached data if Garmin unavailable — notes staleness to user
- GitHub Actions runs daily sync as fallback (see GitHub Actions section)

### 2. Plan Creation ✓
- Triggered via `POST /plan/create`; user clicks "Create Plan" button in UI
- Uses **native Anthropic tool_use API** with Claude Opus 4-7 in an agentic loop (up to 10 iterations) — unlike the rest of the system which uses the JSON plan approach
- Tools available to the plan creator: `pacing_calculator`, `query_data`, `get_course_details`, `save_training_plan`
- Plan creator calls tools to gather context (pacing zones, recent training load, course terrain), then calls `save_training_plan` as final action
- **Guardrails**: before saving, `challenger()` in `services/guardrails.py` validates the plan against hard rules (adjacent hard days, long run monotonicity, mileage ramp ≤20%, peak long run timing, taper structure). If violations found, sends them back as a tool result and loops once more. Validates at most once — second attempt always saves.
- `PLAN_CHECKER_SYSTEM` prompt defines 11 hard + quality rules for the challenger
- Persists to `current_plan` + `plan_days` + `plan_intervals` via `save_plan()`
- System prompt (`CREATE_PLAN_SYSTEM`) is prompt-cached (ephemeral)

### 3. Get Plan ✓
- Default timeframe: current week
- Temporal grounding injected in planner (week_day_map with exact ISO dates for Mon-Sun)
- Returns `plan_days` rows for the requested window; includes `plan_overview` with race metadata
- Passes to final LLM with `get_plan` TOOL_SNIPPET for plain English summary

### 4. Clear Plan ✓
- `DELETE /plan/delete` — deletes `current_plan` row (cascades to `plan_days` + `plan_intervals`)
- User directed to "Delete Plan" trash icon in UI; not done via chat

### 5. Update Plan ✓
- `POST /plan/sync` for activity reconciliation; triggered by chat intent otherwise via `update_plan` tool
- `UPDATE_PLAN_SYSTEM` prompt handles all cases: illness (mild/moderate/severe), injury, skipping, reconciliation with actual activities
- **Scope**: ±7 days from today only. Returns `{"changes": []}` if outside window; coach directs user to click the day or regenerate plan
- `include_activities=true` fetches Garmin activities for the same window and passes them to the LLM for reconciliation
- LLM outputs `{"changes": [...]}` list; `update_plan_day()` applies each change deterministically
- **Undo history**: when `update_plan_day()` changes a `workout_type`, it reads the current plan day from DB and prepends `"Was: {WORKOUT_TYPE} {miles}mi @ {pace}"` to notes (only if notes don't already start with `"Was:"`). For INTERVAL days, the full intervals JSON is appended. This enables one-step revert via `REVERTING A DAY` rule in `UPDATE_PLAN_SYSTEM`.
- Weekly automatic refresh: GitHub Actions runs `services/plan.py __main__` every Monday 8am UTC, passing ACWR + race date into the intent for load-aware adjustments

### 5a. Manual Day Edit ✓
- `PATCH /plan/day/{day_id}` — partial update of a single plan day (workout_type, target_miles, target_pace, notes, intervals)
- `DELETE /plan/day/{day_id}` — sets the day to REST with all fields nulled (does not delete the row)
- Triggered from UI: click a plan day → "Edit" button top-right of modal → edit form pre-filled with current values → Save / Clear / Cancel
- `patch_plan()` in `db/plan.py` filters None values before writing; conditionally replaces intervals if provided

### 6. Pacing Calculator ✓
- Used by plan creation or direct user request
- Zones (Daniels-style offsets from equivalent marathon pace via Riegel formula): easy (+1:30), aerobic (+0:45), marathon (0), threshold (-0:15), interval (-1:00), repetition (-1:30)
- Also returns `goal_pace`, `gps_adjusted_pace` (+2.5% for tangent/GPS drift), `current_easy_pace` (ACSM formula from VO2 max)
- `current_easy_pace`: derived from **30-day average** VO2 max (not latest value — Garmin VO2 fluctuates with heat/fatigue). Coach only flags the gap if it's >60s/mile from goal easy pace.
- Implemented in `services/pacing.py`

### 7. Get Weather ✓
- Triggered if user asks about weather or whether to run inside
- WeatherAPI.com free tier — hardcoded location (`LOCATION` env var) for V1
- Returns current conditions + next 12 hours (temp, feels like, wind, humidity, chance of rain, condition)
- Past/future dates beyond today gracefully rejected with explanation
- Hourly data passed to final LLM; TOOL_SNIPPETS guide the response (best time window, treadmill suggestion, what to wear)

### 8. Update Preferences ✓
- Updates a single training preference field when the user explicitly asks
- Fields: `days_per_week` (int), `preferred_days` (list of day names), `avg_miles` (float), `max_miles` (float), `time_based` (bool)
- Planner emits one `update_preferences` tool call per field; multiple changes in one message → multiple sequential calls
- Writes via upsert on `training_preferences` table; returns `{"status": "success"}` or error dict
- Final LLM confirms naturally ("Done, I've updated your training to 5 days a week")
- If update fails, user directed to Edit Training Preferences button

### 8a. Set Athlete Notes ✓
- Saves free-text athlete notes to `training_preferences.notes` via `db/preferences.py::set_notes()`
- Notes field is injected into the weekly plan refresh intent as a mandatory block that takes precedence over all other rules
- Used to persist injury history, training constraints, and personal preferences across sessions

### 9. Get Race ✓
- Returns the user's upcoming race details: race type, race date, goal time, distance in miles
- Used for race prep questions, taper advice, time-until-race calculations, and general race context
- Reads from the `race` table; returns empty dict if no race set

### 10. Race Prep Info ✓
- Injects `knowledge/race_prep.md` into the final LLM system prompt when triggered
- Covers: pre-race nutrition, race morning routine, warm-up protocol, pacing strategy, and gear
- Loaded once at startup in `services/final.py` (`RACE_PREP_KNOWLEDGE`); injected as `[race_prep_knowledge]` block when the tool runs
- Only triggered when the user explicitly asks about race day prep, nutrition, warm-up, or how to execute the race — not for general race questions

### 12. Query User Data ✓
- Haiku call (via `sql_selector`) determines which query functions to run and what timeframes
- Returns data as plain English with light knowledge context
- Reroute to Garmin sync form if no data

### 13. Trend Analysis ✓
- 14 per-metric trend functions in `services/trend_analysis.py` (miles, pace, hr, calories x2, count, time, hrv, rhr, sleep score, sleep hours, stress, steps x2)
- Each compares current window against the same-length window shifted back 30 days
- Returns `{metric, current, previous?, trend: "improving"|"declining"|"stable"}` — comparison fields skipped if prev window has no data
- Registered in `sql_selector.REGISTRY` alongside raw fetchers; Haiku picks one or many based on intent
- Trend functions internally call the cached `get_activities`/`get_health_history` — no extra DB calls
- **Per-user MIN_DATE** enforced in db layer: `get_user_min_date(user_id)` in `db/health_history.py` queries both `health_history` and `activity_history` for the user's earliest `calendar_date` row, falling back to `"2020-01-01"` if none found. This date is passed to the planner and final LLM so queries are bounded to data that actually exists. If the requested start is before MIN_DATE, the window shifts forward (preserving length); if the entire range is before MIN_DATE, returns `[]`. Final LLM is told to inform the user when shifting occurs.

### 14. Get Course Details ✓
- Anthropic web search + planner LLM-generated query
- Second LLM call (Haiku) to consolidate search results into plain English summary
- RAG via `knowledge/course_chunks.json`: first tries word-overlap (≥0.7 Jaccard), then Voyage embedding similarity (threshold 0.7). Falls back to web search on miss.
- Voyage client lazily initialized (only if `VOYAGE_API_KEY` is set) to avoid import-time crashes in environments without the key
- Cached locally in `course_chunks.json` with embeddings inline

### 15. Race Time Prediction
- XGBoost model (see ML Model section)
- Not current priority — V2 feature

### 16. Search Race Info ✓
- Web search for time-sensitive race data: registration dates/status, entry fees, lottery odds, race-day start time/location
- Do NOT rely on model knowledge for this — standards and dates change yearly
- Always caveats results and directs user to verify on official race website
- Args: `{race, location, info_type, query}` — info_type is `registration` | `race_day`; query is the specific aspect (e.g. "qualifying standards 2026")
- **Caching**: cache in `search_cache` (Supabase), partitioned by (topic, race, location, info_type), 365-day TTL for race_info. Fuzzy match on cache read via word overlap, falling back to Voyage embedding similarity.

### 17. Get Race Results (planned)
- Anthropic web search to find Athlinks event ID
- Ask user for bib number
- Extract event ID from search result URL via regex
- Compare with user target time if same distance
- Encouraging final LLM response either way
- If no data found: state no access gracefully

### 18. Compute Body Battery ✓
- Recovery readiness score computed from today's sleep hours, yesterday's stress, today's HRV, and the past 24h of activity load
- Returns `{body_battery, sleep_hours, stress, hrv, num_activities, last_activity}` — component values included so LLM can caveat missing data or account for a hard run today
- `sleep_hours`, `hrv`, `stress` are **3-day recency-weighted averages** via `_weighted_avg()` (weights: today=1.0, yesterday=0.67, 2 days ago=0.5). Zero/missing days excluded from average.
- Activity load multipliers: `(0.75, 0.5, 0.3)` for hard/moderate/easy effort — calibrated to avoid over-penalizing single hard runs
- `last_activity` = most recent activity dict (or null) — if today, LLM factors it into recovery recommendation regardless of battery score
- Dashboard shows `0` when battery is zero (previously hidden); guarded against negative values
- Implemented in `services/trend_analysis.py::compute_body_battery(user_id)`
- Also exposed as `GET /health/body-battery` route for dashboard display
- Registered in `sql_selector.REGISTRY`; Haiku instructed to use only for readiness/recovery questions

### 19. Compute Training Load ✓
- Returns `{acute_load, chronic_load, acwr}` — acute=last 7 days, chronic=28-day weekly avg, ACWR=acute/chronic (flag >1.3)
- Activity load formula: `total_minutes × (avg_hr / max_hr)`
- Implemented in `services/trend_analysis.py::compute_load(user_id)`
- Registered in `sql_selector.REGISTRY` alongside body battery; same sparing-use rules

---

## LLM Flow

There is no orchestrator. All logic lives in one flat `services/` layer. The planner LLM (Sonnet) runs on every question and decides the path; downstream steps execute deterministically or via Haiku. The final LLM (Sonnet) generates the response.

### End-to-End Per Question

```
User question
└── Planner LLM (Sonnet) — runs on every question
    ├── no tools needed
    │   → final LLM (Sonnet) — 2 calls total
    │
    └── tools needed
        → tool execution in declared order (deterministic)
        → some tools call sql_selector (Haiku) internally to fetch data
        → final LLM (Sonnet) with per-tool snippets in system prompt — 2-3 calls total
```

### Coach Service (single-shot planner, deterministic chaining)

The planner runs **once**, outputs a JSON plan listing every tool to call (with args and order), and Python executes the plan deterministically against the `db/queries.py` REGISTRY. There is no agentic loop and no back-and-forth between the model and the tool layer.

```python
# models/planner.py
from typing import Literal
from pydantic import BaseModel

class ToolStep(BaseModel):
    name: str
    args: dict = {}

class PlannerOutput(BaseModel):
    reasoning: str
    path: Literal["no_tools", "tools"]
    tools: list[ToolStep] = []

# services/coach.py
def ask(question: str, user_id: str) -> str:
    # 1. Planner — ONE Sonnet call. Raw JSON string returned by the model.
    raw = planner_llm(question, user_id)

    # 2. Pydantic-validate the planner's JSON before using it.
    #    Catches: missing fields, wrong types, invalid path enum, malformed JSON.
    plan = PlannerOutput.model_validate_json(raw)

    # 3. Validate tool names against TOOL_METADATA
    for step in plan.tools:
        if step.name not in TOOL_METADATA:
            raise ValueError(f"Unknown tool: {step.name}")

    # 4. Branch on path — no LLM-in-the-loop
    if plan.path == "no_tools":
        return final_llm(question)                                  # 2 LLM calls total

    results = []
    ran_tools = []
    for step in plan.tools:                                         # deterministic execution in declared order
        fn = TOOL_REGISTRY[step.name]
        # Some tools (e.g. get_plan, trend_analysis) call sql_selector internally.
        # That's per-tool, hidden inside the function. coach.py never calls sql_selector directly.
        results.append(fn(user_id, **step.args))
        ran_tools.append(step.name)
    return final_llm(question, results, snippets_for=ran_tools)     # 2-3 LLM calls total
```

Two-layer validation — **Pydantic** catches structural problems in the LLM's JSON (typos, missing fields, wrong enum); the **REGISTRY check** catches semantic problems (tool name doesn't exist). On any `ValidationError` or `ValueError`, fall back to a direct final-LLM call (per Guardrails: "if all tool names invalid, fallback to direct LLM response").

### Planner Prompt — Built From TOOL_METADATA

The planner's tool list is built from `TOOL_METADATA` in `services/prompts.py`. Adding a tool = add one entry to `TOOL_METADATA` and wire up the callable in `services/coach.py::TOOL_REGISTRY`.

```python
# services/prompts.py
def build_planner_system() -> str:
    today = date.today().isoformat()
    tool_list = "\n".join(f"- {name}: {desc}" for name, desc in TOOL_METADATA.items())
    return f"""...
Available tools:
{tool_list}
Today's date: {today}
Return ONLY valid JSON:
{{
  "reasoning": "...",
  "path": "no_tools" | "tools",
  "tools": [{{"name": "tool_name", "args": {{}}}}]
}}"""
```

### Why JSON Plan, Not Anthropic's `tool_use` API

| | JSON plan (chosen) | Native `tool_use` |
|---|---|---|
| Validate before execution | Yes — bad tool names caught before any DB call | Only after Claude tries to call it |
| Deterministic ordering | Explicit, controllable in Python | Up to the model |
| Inject logic between steps | Easy (e.g. always run Garmin sync first) | Requires prompt engineering or a wrapper loop |
| Debuggability | Print full plan before run | One tool call visible at a time |
| Loop with model | None (single shot) | Yes (extra API calls, harder to bound cost) |
| Fits this codebase | Aligns with REGISTRY + deterministic ethos | Designed for exploratory agentic flows |

**Exception — Plan Creation**: `create_plan()` in `services/plan.py` uses native `tool_use` with Claude Opus 4-7 in a multi-turn agentic loop (up to 10 iterations). This is intentional: plan creation requires the model to gather context (pacing zones, training history, course details) before writing the full plan, and the guardrails step may force a revision loop. The JSON-plan approach can't support this kind of back-and-forth; native tool_use is the right fit here. All other coach interactions remain JSON-plan based.

### LLM Call Budget

| Question type | LLM calls | Breakdown |
|---|---|---|
| No tools needed | 2 | Planner (Sonnet) + final (Sonnet) |
| Analytical / multi-source | 2-3 | Planner (Sonnet) + final (Sonnet); +1 Haiku if a tool calls one internally (e.g. web search summarisation) |

### Future: Orchestrator (V2+)

An orchestrator only makes sense when there are multiple **separate deployed services** to coordinate — e.g. a nutrition app, a calendar app, a recovery app each with their own databases and APIs. At that point this entire app becomes one tool the orchestrator calls. Not needed now.

---

## Caching Strategy

### L1 Cache — Range-Aware TTLCache

DB query results (health, activities) are cached in-memory per session with range awareness:

- Cache keyed by `user_id:query_type` (e.g. `user123:activity_data`)
- Each entry stores `{start, end, data}` — multiple non-overlapping ranges per user
- On lookup: find any entry where `entry.start ≤ requested_start` and `entry.end ≥ requested_end`, filter rows to requested range
- On miss: fetch from DB, append new entry — never overwrites existing entries
- Self-caching: `get_health_history` and `get_activities` handle cache check/set internally — callers are cache-unaware
- TTL: 1 hour (`ttl=3600`)

### L2 Cache — Supabase search_cache

For race info (implemented). Course details cached separately in `course_chunks.json`. Weather is not cached anywhere — live API call on every request.

### Expiry by Topic (L2)

| Topic | L2 Expiry | Status |
|---|---|---|
| race_info | 365 days | implemented |
| race_results | 365 days | planned |
| weather | — | not cached, by design — low volume, live API call every request |
| elevation | 90 days | planned |
| course_details | 60 days | planned — separate cache exists today (`course_chunks.json`), not in search_cache |

---

## ML Model

### Goal

Predict marathon/half marathon finish time in seconds based on training inputs.

### Input Features

| Feature | Source | Window |
|---|---|---|
| Peak weekly mileage | activity_history | 16 weeks |
| Average weekly mileage | activity_history | 16 weeks |
| Total mileage | activity_history | 16 weeks |
| Longest long run | activity_history | 16 weeks |
| Average long run | activity_history | 16 weeks |
| Average HRV | health_history | 30 days |
| Resting HR trend (slope) | health_history | 8 weeks |
| VO2 max | health_history | latest |
| Average easy pace | activity_history | 30 days |
| Average long run pace | activity_history | 16 weeks |
| Training load | activity_history | 4 weeks |

### Model

XGBoost Regressor — handles small datasets well, interpretable, no neural net overhead.

### Cold Start Strategy

Three phases based on number of personal races:

```
0-5 races:   Riegel formula only
             T2 = T1 × (D2 / D1) ^ 1.06

5-10 races:  Blended (60% Riegel + 40% XGBoost)

10+ races:   XGBoost only — personalised to athlete
```

### Retraining

- Triggered automatically every 3 new races with logged actual times
- `model_predictions` table stores: prediction, actual, feature snapshot, model version
- Hot reload on retrain — no server restart needed
- Model versioned: `finish_time_predictor_v{N}.json` + `latest` symlink

### File Structure

```
services/ml/
├── features.py     # pulls + shapes data from Supabase
├── train.py        # run: python services/ml/train.py
├── predict.py      # model loaded once at startup
├── retrain.py      # threshold check + hot reload
└── evaluate.py     # MAE tracking over time

models/
├── finish_time_predictor_v1.json
└── finish_time_predictor_latest.json
```

---

## Guardrails & Validation

### Pydantic Validation
- **Planner LLM output validated** via `ToolPlan` Pydantic model (`models/planner.py`) before any tool dispatch — catches malformed JSON, missing fields, wrong types, invalid `path` enum
- Garmin data is not validated with Pydantic — the API shape is stable and helper functions handle type coercion (`_to_int`, `_seconds_to_interval`, `_mps_to_pace`)

### Input Guardrails ✓
- **`services/guardrails.py::input_check(query)`** — runs before planner on every message, returns `(blocked: bool, message: str)`
- Query too short (< 2 chars after strip) → "Looks like you got cut off"
- Query too long (> 150 words) → "That message is a bit long"
- If blocked: yields message as a `("chunk", msg)` SSE event and returns early — no planner call, no LLM cost

### Deterministic Checks ✓
- SQL outputs must start with SELECT — raise ValueError otherwise
- Plan constraints: no back-to-back hard days, max long run ≤ race distance
- Timeframe constraints: planner cannot schedule beyond race date
- **Tool name validity** ✓ — unknown tool names silently skipped in `orchestrate`; if ALL tools are invalid, `planner_response.path` is set to `"no_tools"` to prevent empty tool results being passed to final LLM

### Prompt Injection Protection
- Main functionality snippet included in both main LLM prompts
- Snippet instructs model to ignore instructions attempting to change its purpose

### Training Load Guardrail
- Stress test before finalising plan: weighted based on past week stress + sleep score
- Flags if planned load exceeds athlete's recent capacity

### Silent Failure Handling
- Tool failures are logged and noted to user but do not crash the system
- One retry per tool call before skipping
- Final response still generated with available data, noting what failed

---

## Memory & Compression ✓

- `History` dataclass (`models/planner.py`): `summary: str`, `recent: list[dict]`, `turn_count: int`
- **Session ownership**: history is passed to `orchestrate()` as a parameter and returned after each turn. `routes/ask.py` owns a `session_memory: dict[str, History]` keyed by `session_id` (UUID generated by frontend). Resets on page reload.
- **Planner context**: last 2 turns from `recent` + `summary` injected into planner user prompt
- **Final LLM context**: last 2 turns + `summary` injected into final user prompt alongside today's date
- **Compression**: fires every 5 turns. Haiku call via `services/memory.py::compress_history()` (max 400 tokens, `cache_system=True`). After compression: `summary` updated, `recent` trimmed to last 1 turn as overlap.

## End Behavior & Follow-up ✓

- `services/end.py::is_end_message` — keyword check (exact phrase + substring). END_WORDS: bye, thanks, thank you, that's all, no thanks, nope, all good, ok.
- `detect_end(query, recent)` — calls Haiku with `END_DETECTION` prompt if keyword match. Returns `bool` via `EndBehaviorClassification`.
- Handled in `routes/ask.py` before calling `orchestrate`. If ending: generates follow-ups, yields `{type: "ended", follow_ups: [...]}` SSE event. History NOT updated (so follow-up questions resume from pre-goodbye context).
- `generate_followups(query, recent)` — Haiku call, returns list of 3 strings. Frontend renders as clickable chips that pre-fill the input box.

## SSE Streaming ✓

- `services/llm.py::stream_llm()` — wraps `client.messages.stream()`, yields text chunks as Claude generates them
- `services/final.py::final_output()` — generator, `yield from stream_llm(...)`
- `services/coach.py::orchestrate()` — generator yielding tuples: `("status", text)` before blocking tool calls, `("chunk", text)` for response tokens, `("done", hist)` at end
- `routes/ask.py` wraps `orchestrate` in a `StreamingResponse` generator that formats tuples as SSE events (`data: {...}\n\n`)
- Frontend reads stream via `fetch` + `ReadableStream`, appends chunks in real-time to the chat bubble
- **Throttled markdown rendering**: chunks are accumulated in a buffer and re-rendered via `marked.parse()` on a 150ms debounce timer (`scheduleRender()`). Prevents layout thrash from per-token DOM updates while still feeling real-time.

---

## Error Handling & Retry Logic

All LLM calls go through `call_llm()` in `services/llm.py`:

```
RateLimitError      → retry up to 3 times, exponential backoff + jitter
APIStatusError 5xx  → retry up to 3 times, exponential backoff + jitter
APIStatusError 4xx  → do not retry (bad request won't fix itself)
APIConnectionError  → retry up to 3 times
```

Backoff formula: `wait = (2 ** attempt) + random.uniform(0, 1)` seconds

Garmin rate limit: 100 req/min. 60s wait on rate limit hit. Up to 3 attempts.

---

## External APIs

### Garmin Connect (unofficial `garminconnect` library)

- **Auth**: email/password login; session tokens saved to `.garmin_tokens` and reused to avoid rate limiting
- **Health API**: daily summaries, sleep, HR, HRV, stress, VO2 max
- **Activity API**: per-activity data (distance, pace, HR, duration, type)
- **Rate limit**: sleep 2s between days, 1s between calls within a day to avoid 429s
- **Timestamps**: `calendar_date` pre-calculated in local time by Garmin — use this for day-level grouping
- **Backfill**: supports arbitrary date ranges via `get_activities_by_date` + daily stat calls

### WeatherAPI.com

- **Free tier**, API key required (`WEATHER_API_KEY` env var)
- **Endpoint**: `forecast.json` — returns current + hourly data
- **Coverage**: current day only (free tier); returns next 12 hours from current time
- **Fields used**: temp_f, feelslike_f, wind_mph, wind_dir, humidity, chance_of_rain, condition text
- **Location**: hardcoded via `LOCATION` env var for V1

### Anthropic Web Search

- Built into Claude API via `web_search_20250305` tool
- Used for: course details, race results, Athlinks event ID extraction, general race info
- Returns synthesised text — no URL parsing needed for most use cases
- Results cached in `search_cache` with topic-appropriate expiry

### Athlinks

- Accessed via Anthropic web search (no direct API integration)
- Event ID extracted from search result URL via regex: `r'event/(\d+)'`
- Bib lookup URL: `athlinks.com/azp/ctlive/event/{event_id}/bib/{bib}`

---

## GitHub Actions

### Daily Garmin Sync

```yaml
# .github/workflows/garmin_sync.yml
on:
  schedule:
    - cron: '0 6 * * *'    # 6am UTC daily
  workflow_dispatch:
```

- Runs `python -m services.garmin` with `SUPABASE_URL`, `SUPABASE_KEY`, `GARMIN_EMAIL`, `GARMIN_PASSWORD` secrets
- Keeps DB fresh without requiring app to be running 24/7. Webhook remains primary — this is the fallback.

### Weekly Plan Refresh

```yaml
# .github/workflows/weekly_plan_refresh.yml
on:
  schedule:
    - cron: '0 8 * * 1'    # Monday 8am UTC (~4am EDT)
  workflow_dispatch:
```

- Runs `python services/plan.py` (the `__main__` block), looping over all `USER_IDS`
- Computes ACWR from `compute_load()`, fetches race date, builds a load-aware intent string covering last week's activities vs plan and this week's adjustments, per user
- Intent enforces: ACWR-gated load reduction, ≤10% weekly mileage variance, ≤20% week-over-week increase, long runs flat or increasing, preferred training days respected
- Skips a user with no active plan (`update_plan` returns early) rather than burning an LLM call against an empty plan
- Requires `PYTHONPATH: ${{ github.workspace }}` so relative imports resolve correctly
- Secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `USER_IDS`, `ANTHROPIC_API_KEY`

---

## Implementation Order

### Phase 1 — Foundation (Week 1, Mon–Wed)

1. **Create repo + file structure** — scaffold all directories and empty files as per File Structure above
2. **Set up Supabase** — create project, connect Python client, store URL/key in `.env`
3. **Create PostgreSQL tables** — all schemas defined in this document, in dependency order: users → race → training_preferences → activity_history → health_history → current_plan → plan_history → plan_days → plan_intervals → model_predictions → search_cache

### Phase 2 — Data Layer (Week 1, Thu–Fri)

4. **Garmin API extraction** — `garminconnect` library, token caching, Health API + Activity API endpoints, upsert to Supabase ✓
5. **API keys + model call** — `.env` setup, Anthropic client, verify `call_llm()` works end-to-end with retry logic ✓

### Phase 3 — Core LLM (Weekend 1)

6. **CLI implementation** — simple terminal interface to test LLM calls before building UI ✓
7. **LLM flow implementation** — planner, tool routing, prompt snippets, final LLM call, coach orchestrator ✓
8. **Tool implementation** — Get Weather ✓ → Query User Data ✓ → Garmin Sync ✓ → Trend Analysis ✓ → Body Battery ✓ → Training Load ✓ → Pacing Calculator ✓ → Get Course Details ✓ → Update Preferences ✓ → Get Race Results → Plan tools

### Phase 4 — Agent + Output (Week 2, Mon–Tue)

9. **Final call + end behaviour + follow-ups** ✓ — keyword detection + Haiku confirmation; follow-up chips rendered in frontend; conversation history + Haiku compression every 5 turns
10. **SSE streaming** ✓ — `stream_llm()` + `final_output` as generator + `orchestrate` as generator + `StreamingResponse` route; status events between tool calls
11. **Server side** ✓ — FastAPI + CORS + StaticFiles; `routes/ask.py` (SSE), `routes/activities.py` (health, sync, weather endpoints)
12. **Remaining tools** — Get Race Results → Plan Creation → Update Plan → Clear Plan → Get Plan

### Phase 5 — Frontend ✓

13. **Design** — single-file `static/index.html` served from FastAPI
14. **Frontend implementation** ✓ — health chart, activity card, weather widget, chatbot with SSE streaming, follow-up chips, Garmin sync popover

### Phase 6 — Launch Prep ✓ (in progress)

14. **Auth + login** ✓ — Supabase Auth email/password login; `POST /auth/login` returns JWT; `get_current_user` FastAPI dependency validates token on every route; `static/login.html` login page; all public `users` table FK constraints migrated to `auth.users(id) ON DELETE CASCADE`; public `users` table dropped
15. **Docker + AWS** — Dockerise FastAPI app, deploy to EC2 with nginx reverse proxy

---

## V2 Scope

| Feature | Notes |
|---|---|
| Orchestrator agent | Only if expanding to multiple separate apps (nutrition, calendar etc) |
| Race time prediction (ML) | XGBoost model, Riegel fallback, post-race retraining |
| Input plan to Google Calendar | Use current calendar for time constraints in plan tool |
| Weekly workout knowledge | Pace ranges per workout type as knowledge entries |
| Plan as CSV download | Export current plan via export tool |
| Gmail integration | Send plan as email |
| Enhanced pacing calculator | McMillan/Vdot/Jack Daniels formulas, per workout type |
| Embedding-based course RAG | pgvector in Supabase for similarity search on cached course data |
| Multi-user support | Auth already in place, extend schema for multiple users |
| Activity split data | `get_activity_splits(garmin_activity_id)` per activity during sync — lap-level data (split time, distance, pace, HR). New `activity_splits` table. Enables interval verification against plan, more accurate body battery, split analysis in chat. |
| Heart rate zones (LTHR) | After race results are stored, use avg HR from race efforts as lactate threshold HR (Friel method). Build Friel zones from LTHR — more accurate than % max HR. |

---

*Last updated: June 2026 — reflects uv/ruff/Makefile setup, race_prep_info tool, get_race tool, set_notes, garmin_credentials table, per-user MIN_DATE*
