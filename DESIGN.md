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
| Hosting (app) | Render or Railway | Simple deploy from GitHub, migrate to AWS later |
| Hosting (DB) | Supabase cloud | Already cloud-hosted, no migration needed |
| Weather | Open-Meteo API | Free, no key required, historical + forecast |
| Web search | Anthropic web search tool | Built-in, no extra API key |
| Race results | Athlinks via web search | Event ID extracted from search results |
| CI/CD | GitHub Actions | Daily Garmin sync cron |

---

## File Structure

```
runcoach/
│
├── cli.py                         # CLI entry point (V1) — thin wrapper, calls services/coach.py::ask()
├── main.py                        # FastAPI app entry point (Phase 4) — registers routes/
├── .env                           # API keys, Supabase URL/key, hardcoded user_id for V1
├── requirements.txt
├── DESIGN.md
│
├── routes/                        # Empty in V1; populated in Phase 4 (server)
│   ├── activities.py              # Garmin webhook handler endpoints
│   ├── plan.py                    # Plan CRUD endpoints
│   ├── ask.py                     # Main /ask entry point
│   └── auth.py                    # Auth endpoints (added pre-launch)
│
├── services/
│   ├── coach.py                   # Orchestrator — ask(question, user_id), single-shot planner + dispatch
│   ├── llm.py                     # Central call_llm() with retry + caching
│   ├── planner.py                 # Planner LLM call + ToolPlan validation + REGISTRY-derived prompt
│   ├── sql_selector.py            # Haiku call that picks SQL func + args from REGISTRY (SQL path only)
│   ├── final.py                   # Final LLM call (Sonnet). Builds system prompt from BASE + per-tool snippets.
│   ├── garmin.py                  # Garmin webhook parsing + enrichment
│   ├── plan.py                    # Training plan creation, update, injury logic
│   ├── web_search.py              # Anthropic web search wrapper + persistence
│   ├── weather.py                 # Open-Meteo API wrapper
│   ├── export.py                  # CSV export. Local file in CLI mode, HTTP attachment stream in server mode (V2)
│   ├── cache.py                   # TTLCache singleton + two-layer cache logic
│   └── prompts.py                 # All prompt strings in one place: BASE_COACH, PLANNER_SYSTEM, SQL_SELECTOR_SYSTEM, TOOL_SNIPPETS, per-tool Haiku prompts (web search, course summary), compression, follow-up. Single source of truth for prompt engineering.
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
│   ├── queries.py                 # Registry of every callable SQL function (name → callable + description) — Haiku selects from this list
│   ├── activity_history.py        # Hardcoded queries for activity_history
│   ├── health_history.py          # Hardcoded queries for health_history (merged daily + sleep)
│   ├── race.py                    # Read/write for the user's target race
│   ├── preferences.py             # Read/write for training_preferences
│   ├── plan.py                    # Plan read/write queries (current_plan, plan_days, plan_intervals, plan_history)
│   └── cache.py                   # Supabase search_cache read/write
│
├── models/
│   ├── planner.py                 # Pydantic model for planner LLM JSON output (ToolPlan)
│   └── finish_time_predictor.json # Serialised XGBoost model (V2)
│
├── knowledge/
│   ├── training_zones.json        # HR zones, pace zones — static reference
│   └── race_distances.json        # Standard distances in km — static reference
│
├── tests/
│   ├── test_deterministic.py      # All deterministic logic (plan constraints etc)
│   └── test_integration.py        # End-to-end consistency + temperature tuning
│
└── .github/
    └── workflows/
        └── garmin_sync.yml        # Daily Garmin data sync cron
```

---

## Database Schema

### users
```sql
create table users (
    id          uuid default gen_random_uuid() primary key,
    email       text unique,
    password    text,                -- hashed, Supabase Auth handles this
    created_at  timestamptz default now()
);
```

### race (user's target race — one per user)
```sql
create table race (
    id                  uuid default gen_random_uuid() primary key,
    user_id             uuid references users(id) unique,
    race_description    text,
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
    user_id         uuid references users(id) unique,
    days_per_week   integer default 4,
    preferred_days  text[],                                     -- e.g. {'MON','WED','FRI','SAT'}
    avg_miles       float,
    max_miles       float,
    time_based      boolean default false                       -- false = mile based, true = time based
);
```

### activity_history (per-activity rows from Garmin)
```sql
create table activity_history (
    id                  uuid default gen_random_uuid() primary key,
    user_id             uuid references users(id),
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
    user_id         uuid references users(id) unique,               -- enforces one per user
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
    workout_type    text,                                           -- EASY | LONG | TEMPO | INTERVAL | REST | CROSS
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
    query_hash      text unique,                                    -- MD5 of normalised query
    query           text,
    result          text,
    topic           text,                                           -- race_info | weather | race_results | elevation
    source          text,                                           -- web_search | open_meteo | athlinks
    expires_at      timestamptz,
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
└── POST /ask (routes/ask.py)
    └── Planner LLM (Sonnet) — decides path + tools
        ├── No tools needed → final LLM (2 calls)
        ├── SQL needed      → Haiku picks SQL func + args → execute → final LLM (3 calls)
        └── Tools needed    → tool execution (deterministic; specific tools may call Haiku internally) → final LLM (2-3 calls)
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
    • SQL needed   → Haiku picks which pre-built SQL func to call → execute → final LLM
    • tools needed → tool execution (deterministic; some tools may call Haiku internally) → final LLM
→ Final LLM (Sonnet) — coaching response. System prompt = BASE + per-tool snippets for tools that ran.
→ End behaviour + follow-up logic
→ Persist to DB
```

### Prompt Architecture

**Central call wrapper** — all LLM calls go through one function:

```python
# services/llm.py
def call_llm(system: str, user: str, max_tokens: int = 1000,
             max_retries: int = 3) -> str
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

**Prompt caching** — enabled on all system prompts via `cache_control: ephemeral`. Applied to large race context chunks too, not just system prompts. Minimum 1024 tokens for cache eligibility.

### Routing Logic

```
Question arrives
└── Planner (Sonnet) — every question goes through the planner
    ├── No tools needed
    │   → direct to final LLM
    │
    ├── SQL / data question
    │   → Haiku picks which pre-built SQL func to call → execute → final LLM
    │
    └── Analytical / multi-source (tools needed)
        → execute tools (deterministic; some may call Haiku internally) → final LLM
```

### SQL Query Registry

Haiku does not generate SQL. It picks from a fixed registry of pre-built query functions defined in `db/queries.py`. Each entry is `(name, callable, description)`; the description is what Haiku reads to decide which function fits the question.

```python
# db/queries.py
from db import activities, daily, sleep, plan

REGISTRY = {
    "get_recent_runs":         (activities.get_recent,      "Last N runs by date — supports limit + date range"),
    "get_runs_by_pace":        (activities.get_by_pace,     "Runs filtered by avg pace range"),
    "get_daily_summary":       (daily.get_for_date,         "Daily summary for a single date"),
    "get_sleep_for_date":      (sleep.get_for_date,         "Sleep + HRV for a single night"),
    "get_weekly_mileage":      (activities.weekly_mileage,  "Weekly mileage rollup over a date range"),
    "get_current_plan_week":   (plan.get_current_week,      "Current training-plan week + completion state"),
    # ...one entry per callable query
}
```

Adding a new query = add a function to the relevant `db/*.py` file + add one line to `REGISTRY`. Haiku sees the new option immediately, no prompt change needed.

---

## Tool Suite

### 1. Garmin Sync
- Triggered by webhook on device sync
- Always first if multiple tool calls
- Uses cached data if Garmin unavailable — notes staleness to user
- GitHub Actions runs daily sync as fallback (see GitHub Actions section)

### 2. Plan Creation
- Onboarding entry point; user fills form
- Generates per-day workouts with intervals for all weeks leading to race
- Persists to `current_plan` + `plan_days` + `plan_intervals`
- Archives previous plan to `plan_history` before creating new one
- Output: deterministic confirmation message only

### 3. Get Plan
- Default timeframe: current week
- Temporal grounding injected in planner
- Sub-tool: determines timeframe + which SQL query to call
- Passes to final LLM for plain English summary

### 4. Clear Plan
- Sets all DB entries to null
- Asks user if they want to generate a new plan

### 5. Update Plan
- Injury handling: LLM outputs severity score 1-10, deterministic SQL handles modification:
  - 1-2: swap hard days to easy, keep volume
  - 3-4: cut intensity, keep structure
  - 5-6: cut volume 40%
  - 7+: pause plan, flag medical advice
- Skipped day: LLM reconfigures remaining days
- Timeframe and constraint checks are deterministic

### 6. Pacing Calculator
- Used by plan creation or direct user request
- Common pace formulas for each workout type (easy, tempo, threshold, interval)
- Knowledge entries define pace ranges per workout type

### 7. Get Weather
- Triggered if user asks about weather or whether to run inside
- Open-Meteo API — hardcoded location for V1
- Passed as context with timeframe to final LLM prompt

### 8. Get Race Results
- Anthropic web search to find Athlinks event ID
- Ask user for bib number
- Extract event ID from search result URL via regex
- Compare with user target time if same distance
- Encouraging final LLM response either way
- If no data found: state no access gracefully

### 9. Query User Data
- Second LLM call to determine tables + timeframes (metadata + all possibilities)
- Returns data as plain English with light knowledge context
- Reroute to Garmin sync form if no data

### 10. Trend Analysis
- Only called if user specifically asks for trends or comparisons
- Usually past month comparisons
- Predefined SQL mapped from natural language

### 11. Get Course Details
- Anthropic web search + planner LLM-generated query
- Second LLM call (Haiku) to consolidate search results into plain English summary
- RAG with embedding similarity scores for cached races
- Cache in `search_cache` with 60-day expiry

### 12. Race Time Prediction
- XGBoost model (see ML Model section)
- Not current priority — V2 feature

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
    ├── data/stats question
    │   → Haiku picks pre-built SQL func from registry
    │   → execute query
    │   → final LLM (Sonnet) — 3 calls total
    │
    └── analytical / multi-source question
        → tool execution (deterministic; specific tools may call Haiku internally)
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

class ToolPlan(BaseModel):
    reasoning: str
    path: Literal["no_tools", "sql", "tools"]
    tools: list[ToolStep] = []

# services/coach.py
def ask(question: str, user_id: str) -> str:
    # 1. Planner — ONE Sonnet call. Raw JSON string returned by the model.
    raw = planner_llm(question, user_id)

    # 2. Pydantic-validate the planner's JSON before using it.
    #    Catches: missing fields, wrong types, invalid path enum, malformed JSON.
    plan = ToolPlan.model_validate_json(raw)

    # 3. Validate tool names against REGISTRY (Pydantic can't know about runtime registry)
    for step in plan.tools:
        if step.name not in REGISTRY:
            raise ValueError(f"Unknown tool: {step.name}")

    # 4. Branch on path — no LLM-in-the-loop
    if plan.path == "no_tools":
        return final_llm(question)                                  # 2 LLM calls total

    if plan.path == "sql":
        # Planner identified the SQL path only; Haiku picks the specific query func + args from REGISTRY.
        # This split keeps the planner's prompt short (high-level paths) and delegates fine-grained
        # selection to the cheaper model.
        func_name, args = sql_selector_llm(question, user_id)
        data = REGISTRY[func_name]["callable"](user_id, **args)
        return final_llm(question, data)                            # 3 LLM calls total

    if plan.path == "tools":
        results = []
        ran_tools = []
        for step in plan.tools:                                     # deterministic execution in declared order
            fn = REGISTRY[step.name]["callable"]
            # Some tools (e.g. web_search, course_details) call Haiku internally to summarise their own output.
            # That's per-tool, hidden inside the function. coach.py never calls a "content plan" LLM.
            results.append(fn(user_id, **step.args))
            ran_tools.append(step.name)
        return final_llm(question, results, snippets_for=ran_tools) # 2-3 LLM calls total (3 if any tool used Haiku)
```

Two-layer validation — **Pydantic** catches structural problems in the LLM's JSON (typos, missing fields, wrong enum); the **REGISTRY check** catches semantic problems (tool name doesn't exist). On any `ValidationError` or `ValueError`, fall back to a direct final-LLM call (per Guardrails: "if all tool names invalid, fallback to direct LLM response").

### Planner Prompt — Auto-Generated From REGISTRY

The planner's tool list is generated from `db/queries.py REGISTRY` at app startup, so it stays in sync automatically. Adding a tool = add one line to REGISTRY; the planner sees it on next boot.

```python
def build_planner_system() -> str:
    tool_list = "\n".join(
        f"- {name}({entry['args']}) — {entry['description']}"
        for name, entry in REGISTRY.items()
    )
    return f"""You are a planning assistant for a running coach app.
Given a question, decide which tools to call and in what order.

Available tools:
{tool_list}

Return ONLY JSON:
{{
  "reasoning": "...",
  "path": "no_tools" | "sql" | "tools",
  "tools": [ {{"name": "tool_name", "args": {{...}}}} ]
}}

Today's date: {{today}}"""
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

### LLM Call Budget

| Question type | LLM calls | Breakdown |
|---|---|---|
| No tools needed | 2 | Planner (Sonnet) + final (Sonnet) |
| SQL / data | 3 | Planner (Sonnet) + SQL func selector (Haiku) + final (Sonnet) |
| Analytical / multi-source | 2-3 | Planner (Sonnet) + final (Sonnet); +1 Haiku if a tool calls one internally (e.g. web search summarisation) |

### Future: Orchestrator (V2+)

An orchestrator only makes sense when there are multiple **separate deployed services** to coordinate — e.g. a nutrition app, a calendar app, a recovery app each with their own databases and APIs. At that point this entire app becomes one tool the orchestrator calls. Not needed now.

---

## Caching Strategy

### Two-Layer Cache

```
Request
├── L1: TTLCache (in-memory, per session)   — instant, free, gone on restart
│   hit → return immediately
│   miss ↓
├── L2: Supabase search_cache               — persistent, across sessions
│   hit → populate L1 + return
│   miss ↓
└── External API / web search
    → store in both L1 and L2
```

### TTLCache Configuration

```python
# services/cache.py — singleton imported everywhere
from cachetools import TTLCache
session_cache = TTLCache(maxsize=100, ttl=3600)
```

### Expiry by Topic

| Topic | L2 Expiry |
|---|---|
| race_info | 60 days |
| race_results | 365 days |
| weather | 1 day |
| elevation | 90 days |
| course_details | 60 days |

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

### Deterministic Checks
- SQL outputs must start with SELECT — raise ValueError otherwise
- Plan constraints: no back-to-back hard days, max long run ≤ race distance
- Timeframe constraints: planner cannot schedule beyond race date
- Tool name validity: if all tool names invalid, fallback to direct LLM response

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

## Memory & Compression

- Compression triggered every **5 turns**
- Lightweight Haiku call summarises conversation to predefined max tokens
- Stored entries preserved as exceptions: user current state, active plan context
- Timeframe and timezone context re-injected after compression

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

### Open-Meteo

- **Free**, no API key required
- **Historical**: back to 1940 — enables weather correlation with past runs
- **Forecast**: up to 16 days
- **Fields used**: temperature, apparent temperature, precipitation, wind speed, UV index
- **Units**: Fahrenheit, mph, inches

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
name: Daily Garmin Sync

on:
  schedule:
    - cron: '0 6 * * *'    # 6am UTC daily
  workflow_dispatch:        # allow manual trigger

jobs:
  sync:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python services/garmin.py --sync
        env:
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_KEY: ${{ secrets.SUPABASE_KEY }}
          GARMIN_EMAIL: ${{ secrets.GARMIN_EMAIL }}
          GARMIN_PASSWORD: ${{ secrets.GARMIN_PASSWORD }}
```

Keeps DB fresh without requiring app to be running 24/7. Webhook remains primary — this is the fallback.

---

## Implementation Order

### Phase 1 — Foundation (Week 1, Mon–Wed)

1. **Create repo + file structure** — scaffold all directories and empty files as per File Structure above
2. **Set up Supabase** — create project, connect Python client, store URL/key in `.env`
3. **Create PostgreSQL tables** — all schemas defined in this document, in dependency order: users → race → training_preferences → activity_history → health_history → current_plan → plan_history → plan_days → plan_intervals → model_predictions → search_cache

### Phase 2 — Data Layer (Week 1, Thu–Fri)

4. **Garmin API extraction** — `garminconnect` library, token caching, Health API + Activity API endpoints, upsert to Supabase ✓
5. **API keys + model call** — `.env` setup, Anthropic client, verify `call_llm()` works end-to-end with retry logic

### Phase 3 — Core LLM (Weekend 1)

6. **CLI implementation** — simple terminal interface to test LLM calls before building UI
7. **LLM flow implementation** — planner (Sonnet) on every question, SQL function selector (Haiku) reading from query registry, tool routing, prompt snippets system, content plan step
8. **Tool implementation** — build each tool in Tool Suite section one by one, starting with: Query User Data → Get Plan → Garmin Sync → Get Weather

### Phase 4 — Agent + Output (Week 2, Mon–Tue)

9. **Final call + end behaviour + follow-ups** — final Sonnet call, keyword-based follow-up detection, Haiku follow-up generation
10. **Remaining tools** — Plan Creation → Update Plan → Clear Plan → Get Race Results → Trend Analysis → Get Course Details → Pacing Calculator
11. **Server side** — FastAPI routes wired up, webhook handler live, Render/Railway deploy

### Phase 5 — Frontend (Weekend 2)

12. **Design** — wireframes via Pencil.ai
13. **Frontend implementation** — vibe code initial build, manually edit + refine

### Phase 6 — Launch Prep

14. **Auth + login** — Supabase Auth, password protection, user_id foreign keys active
15. **Docker + AWS** — Dockerise FastAPI app, deploy to EC2 or Elastic Beanstalk, Supabase stays cloud-hosted

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

---

*Last updated: May 2026*
