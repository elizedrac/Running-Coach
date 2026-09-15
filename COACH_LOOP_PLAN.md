# Planner as a tool loop: implementation plan

Working doc. Unlike its predecessor this one is tracked, so it travels with the branch.
Delete once the loop is merged and the flag is removed.

## Context

The planner decides everything in one shot, before any tool has run. It picks which tools
to call and with what args having read nothing: no plan, no activity history, no
preferences. Two consequences, both visible in production:

- **Writes are decided blind.** "I'm sick" makes the planner choose `update_plan` without
  ever seeing which days it is about to change. The confirmation gate in
  `TOOL_METADATA["update_plan"]` exists purely to paper over this: it tells the planner to
  call `get_plan` *instead*, so a later turn can ask the user. A whole turn spent working
  around the fact that the decision came before the data.
- **Args are guessed.** `get_course_details` and `get_race_info` need a race and location.
  The planner infers both from conversation text. Those strings are the `search_cache`
  partition key with a 365-day TTL, so a guess that says "NYC" instead of "New York City"
  splits the cache and forces a refetch that then sticks around for a year.

Make the decision phase iterative instead. The model calls read tools, sees what comes
back, and only then decides what to write.

## Shape

```
orchestrate
│
├── LOOP  (replaces the planner)
│   ├── model calls READ tools, accumulating tool_results across turns
│   └── exits by declaring the WRITE tools it wants, or by having nothing to write
│
├── execute writes deterministically      ← ordering, locks, validation unchanged
│
└── final_output(tool_results + write results)  → streams the answer
```

Two properties worth stating plainly, because they are what make this safe:

1. **No write ever executes inside the loop.** Write tools are in the schema array so the
   model can declare them with validated args, but the dispatcher records them and refuses
   to run them. Every lock, every ordering rule and the per-day validation stay exactly
   where they are today.
2. **`final_output` keeps building the same prompt.** Its inputs change shape (tool results
   become a list, below) but every snippet and knowledge block stays exactly where it is.
   This is the big simplification over the previous plan: `TOOL_SNIPPETS` stay in the user
   prompt, there is no second copy to drift, and the whole "move the snippets into a cached
   system block" question disappears.

## Tool inventory

| Class | Tools | Where they run |
|---|---|---|
| Read | `get_plan`, `query_data`, `get_race`, `get_preferences`, `get_weather`, `pacing_calculator`, `get_course_details`, `get_race_info` | Inside the loop |
| Write | `update_plan`, `update_preferences`, `update_settings` | Declared in the loop, executed after |
| Special | `garmin_sync` | Declared in the loop, executed after, keeps its existing lock/progress/cancel path |
| Pseudo | `race_prep_info` | Not a tool. It is `lambda user_id, **kwargs: None` (`coach.py:65`) and exists only to flag `final.py:86` to inject `RACE_PREP_KNOWLEDGE`. Keep it as a declarable no-op so that flag still fires, but **return a short string instead of `None`**: nothing reads the result today, whereas the loop hands it straight back to the model, and a literal `null` reads as a failed call it should retry. |

`trend_analysis` is in `TOOL_REGISTRY` as an alias for `_query_data` but has never been in
`TOOL_METADATA`, so the planner cannot emit it and the check for it at `final.py:75` is
already dead. Leave it out of `COACH_TOOLS`.

`get_course_details` and `get_race_info` both write a cache row on a miss
(`course_chunks.json` and `search_cache`). That is a side effect, but not a user-visible
mutation and not lock-guarded, so they stay read-class.

## Tool ordering

### Stays in Python, unchanged

Both live in `orchestrate` and both concern writes, which still execute outside the loop:

- `update_preferences` sorted ahead of `update_plan`, so the latter never reads stale prefs
- `garmin_sync` forced first, with its `on_day` progress callback and mid-sync cancel

Do not turn these into prompt rules. They are correctness constraints and Python enforces
them for free.

### Becomes a prompt concern

`build_planner_system` has no ordering section, and the order read tools ran in was arbitrary
model output that Python happened to honour. One rule does already exist, inside a tool
description: `TOOL_METADATA["get_plan"]` ends "Call alongside query_data for 'should I run
today' or recovery questions." Three rules are worth stating explicitly in the loop system
prompt, because the loop makes them actionable for the first time:

- **`get_race` before `get_course_details` or `get_race_info`.** Fetch the real race and
  location instead of guessing them, which is the cache-poisoning fix.
- **`get_preferences` before advising on scheduling, volume or workout swaps.** It carries
  the athlete notes: injuries, constraints, things they asked to be remembered.
- **`get_plan` and `query_data` together for "should I run today" and recovery questions.**
  One says what is scheduled, the other what was actually done. Either alone is wrong.

Plus a cost rule: call independent tools in the **same** turn. Parallel `tool_use` blocks
cost one round trip; one tool per turn costs several.

**State each rule twice: in `COACH_LOOP_SYSTEM` and in the description of the tool it
constrains.** The model reads descriptions while scanning the tool list and the system
prompt while deciding what to do, and an ordering rule that only lives in one of them gets
missed at the other moment. Concretely:

| Rule | System prompt | Tool description |
|---|---|---|
| `get_race` first | "Call get_race BEFORE get_course_details or get_race_info..." | `get_race`: "...and BEFORE get_course_details or get_race_info, so you pass their real race rather than a guess." |
| Notes before advice | "Call get_preferences BEFORE advising on scheduling, volume or workout swaps." | `get_preferences`: "...Call BEFORE advising on scheduling, volume or workout swaps — advice written before reading the notes has to be walked back." |
| Plan + data together | "For 'should I run today' or any recovery question, call get_plan AND query_data before answering." | `get_plan`: "Call alongside query_data for 'should I run today' or recovery questions." |

Both halves are already proven here: `TOOL_METADATA["get_plan"]` carries an ordering rule in
a description today, and `PLAN_CREATOR_TOOLS` does the same in a real schema
(`pacing_calculator`'s description ends "Call this first.").

## Prompting: what changes

### Dies

**`build_planner_system`** (~65 lines). Its contents redistribute rather than disappear:

| Piece | Goes to |
|---|---|
| Tool list built from `TOOL_METADATA` | `COACH_TOOLS` schemas |
| "Args contracts" section | Each tool's `input_schema` property descriptions |
| Weekday table + date interpretation rules | `COACH_LOOP_SYSTEM` |
| The short-affirmation rule ("user says 'yes', call the tool") | `COACH_LOOP_SYSTEM` |
| `no_tools` / `tools` path enum | Gone. The loop either calls tools or answers. |
| "Return ONLY valid JSON" + output shape | Gone. Native `tool_use`. |

**`extract_json` leaves the coach's path.** Native `tool_use` returns structured blocks, so
there is no model text to parse. Leave the helper in place: `sql_selector`,
`write_selector`, `race_info`, `course_details`, `end` and `plan` still use it.

**`PlannerOutput` / `ToolPlan`** (`models/planner.py`) leave the coach's path entirely. They
stay in the file for anything else that imports them, but neither the loop nor
`final_output` needs them once tool results become a list (below). Nothing of value is lost:
`PlannerOutput` only ever checked that `path` was one of two literals and that `args` was a
dict. Real validation lives at dispatch (see Mechanics), and the API does **not** enforce
`input_schema` against the args the model sends, so it is still needed.

### Tool results become a list

This is the one change to `final.py`, and it is what makes repeat calls safe.

Today `tool_results` is a `dict` keyed by tool name, so a second call to the same tool would
overwrite the first. `coach.py:247` avoids that by converting the value to a list and
appending, and `_planned_total` then flattens that list and sums every row it finds
(`final.py:26-34`) with no idea the rows came from two different date ranges. Ask for this
week and next week and the coach reports a two-week sum as one week's mileage.
`final_output` also emits one block per entry in the planner's tool list, so the merged
result gets printed twice.

Repeat calls are rare planner output today and become normal the moment the model can
iterate, so the shape has to change:

```python
calls: list[tuple[str, Any]]   # (tool_name, result), in call order
```

- `final_output` takes `calls` instead of `planner_decision` + `tool_results`, and branches
  on `if calls:` rather than `path == "tools"`. That also absorbs `coach.py:264`, which
  today flips `path` to `no_tools` when every tool failed.
- One block per call, not per name, in the order they ran.
- `_planned_total` receives a single call's rows, so it can drop the nested-list flattening
  that only ever existed to cope with the merge.
- Both paths build the list, so the planner path behind the flag keeps working unchanged.

Two things to keep an eye on while writing it: with two `get_plan` calls there are now two
`[plan/planned_total]` blocks, so the label needs to name its date range for the
`TOOL_SNIPPETS["get_plan"]` "that total is authoritative" line to stay unambiguous; and
`get_current_plan(user_id)` needs the same once-only guard as `health_added` so
`[plan/race_meta]` is not fetched and injected twice.

### Moves

**`TOOL_METADATA`** becomes the `COACH_TOOLS` descriptions. This is a genuine merge, not a
duplication: once the planner is gone there is exactly one place a tool is described, so
the drift risk the previous plan introduced never appears.

Rewrite while moving. `TOOL_METADATA` entries are written to help a model choose from a
list in one shot. Schema descriptions should be prescriptive about *when* to call and what
not to guess, and should absorb the arg discipline currently sitting in the Args contracts
(the `goal_time` hard rule, the fully-spelled-out location and race strings).

### Stays put

- **`TOOL_SNIPPETS`** — `final_output` still runs and still builds the same prompt.
- **`BASE_COACH`** — unchanged. Its rules are about the answer, not about tool selection.
- **`build_update_plan_system`, `CREATE_PLAN_SYSTEM`, `PLAN_CHECKER_SYSTEM`** — untouched.
  `create_plan` keeps its own Opus loop.

### New

**`COACH_TOOLS`** — 8 read schemas, 3 write schemas, `garmin_sync`, and `race_prep_info` as
a no-arg no-op. Write schemas need the same arg contracts `build_planner_system` carries
today, notably `update_plan`'s `intent` string and the undo/revert rule about reading the
day's `Was:` note before asking the user.

**`COACH_LOOP_SYSTEM`** — a builder, not a constant, since it needs today's date. Contents:
the date table and interpretation rules, the tool-order rules above, the short-affirmation
rule, "do not narrate tool use", and the confirmation gate below.

## The confirmation gate

Today's gate is a workaround: `TOOL_METADATA["update_plan"]` tells the planner *not* to call
`update_plan` when a change would clear days the user did not name, and to call `get_plan`
instead so a later turn can ask.

In the loop the model has already read the plan, so the rule becomes what it always wanted
to be: **if the change would clear days the athlete did not name, do not declare the write.
List the days and ask.** Same sentence, but now the model is looking at the days when it
applies it.

Keep the `BASE_COACH` wording that governs how that question is phrased. Keep per-change
validation in `update_plan_day` untouched — it is the backstop, not the gate.

## Files

| File | Change |
|---|---|
| `services/coach.py` | `orchestrate` rewritten around the loop. The write-execution block, `garmin_sync` special case, and `get_plan_id` resolution all survive as the post-loop phase. Keep the no-dates short-circuit (`coach.py:197-203`) exactly as it is: `garmin_sync` declared without a date range sends its fixed "which dates would you like me to pull?" message and returns without calling `final_output`. Deterministic, already well worded, and it saves a call. |
| `services/prompts.py` | Add `COACH_TOOLS` + `build_coach_loop_system`. `build_planner_system` and `TOOL_METADATA` stay until the flag comes out (Rollout step 3). |
| `services/llm.py` | One new helper: a retrying `create` with `tools=`. Reuse `_backoff` (it re-raises on the final attempt, so a `range(MAX_RETRIES)` wrapper cannot fall through to `None`). Put `cache_control: {"type": "ephemeral"}` on the **last tool schema**, not the system block as `call_llm` does at `:49` — everything up to and including the marked block is cached, and system + schemas is the part that never changes while tool results grow behind it. This matters far more here than on the single-shot planner, since the loop resends the whole prefix every turn. |
| `services/planner.py` | Untouched until step 3, then deleted. |
| `services/final.py` | Takes `calls` (a list of `(name, result)`) instead of `planner_decision` + `tool_results`. One block per call, `_planned_total` scoped to one call, `race_meta` guarded against a double fetch. Prompt content otherwise identical. |
| `tests/` | New `test_coach_loop.py`; existing `orchestrate` tests rewritten, since the mocked boundary moves from `planner` to the loop's client. |

`services/chat_stream.py`, the SSE routes, the Redis stream and the frontend are all
untouched, provided the loop keeps yielding the existing event tuples (`status`, `chunk`,
`done`, `plan_updated`, `theme_updated`). `run_chat_job` already forwards `status`, and
`SILENCE_AFTER_STATUS` is 300s against `SILENCE_AFTER_CHUNK`'s 60s, so emitting a status
event before each tool call keeps a slow turn well inside the orphan guard.

## Mechanics to get right

- **History goes in the same way it does today.** The loop's first user message is the
  string `coach.py:174-177` already builds: the question plus a `[Conversation context]`
  block holding `hist.summary` and the last 8 turns, capped at 3000 chars. Not replayed as
  real message turns, since `History` stores truncated strings and the replay would not be
  faithful. This is what makes the short-affirmation rule work at all: "yes" only resolves
  against the turn before it. `final_output` keeps its own separate slice (last 4 turns,
  `coach.py:269-274`), unchanged.
- **Dispatch through `call_tool`, not `TOOL_REGISTRY` directly.** `call_tool` patches args
  the model cannot supply: `get_plan`'s default week, and all of `pacing_calculator`'s
  auto-fill (distance and goal time from the saved race, plus the rule that refuses to pair
  a saved goal time with a different distance). Calling the registry straight drops every
  one of those silently.
- **`get_plan` takes a plan id, not a user id.** `get_plan_id(user_id)` first. Easiest thing
  to get wrong; it silently returns an empty list and the coach says "you have no plan".
- **Strip model-supplied `user_id` and `plan_id` from args** before dispatch.
- **Inject `location` into `get_weather` when the model omits it**, as `coach.py:237` does
  today. The city arrives on the request and the model has no way to know it. Without the
  injection `get_weather` falls back to its own default, a single `LOCATION` env var for the
  whole server, so every user gets that city's weather and nothing errors.
- **Unknown or write tool names return a string, never raise.** The model reads the refusal
  and recovers.
- **Bad args come back as a tool result, not an exception.** The API does not enforce
  `input_schema`, so `fn(user_id, **args)` can still raise `TypeError`. Catch it and return
  the message as the result. Today that same error becomes a string the *final* model has to
  apologize about with the turn already spent; in the loop the model just retries.
- **`update_preferences` needs a real `enum` in its schema** for the five field names. It
  upserts straight into the column (`db/preferences.py:22`) with no validation of its own, so
  today the only thing constraining `field` is prose in the planner prompt.
- **Cache identical read calls within one loop run**, keyed on name plus sorted args.
- **Truncate tool results.** They live in the messages array and are resent every turn,
  unlike today where a large `query_data` payload is sent once. Compute anything derived
  from the full result (`_planned_total`, `build_query_data_extra`) *before* truncating.
- **Bound the loop** and pass no tools on the final turn, so the model must produce an
  answer rather than a tool call there is no budget to run.
- **Do not generate the answer twice.** Once writes are done, `final_output` streams the
  answer. The loop's own turns should never be streamed to the user.
- **Cancellation** is checked between turns as well as between chunks, and a cancelled run
  must not persist a half-finished message list.

## Verification

`make check` for the deterministic suite. Then flip the flag and try, against both paths:

- The original bug: an RHR trend question whose answer names tomorrow's actual scheduled
  workout rather than "if your plan has a rest day".
- "i'm sick" — must read the plan, list the days it would clear, and ask before declaring
  any write.
- "should I run today" — needs `get_plan` and `query_data` in the same turn.
- A registration question for the athlete's own race — must call `get_race` first and pass
  the fully spelled out strings through to `get_race_info`.
- "thanks" — must call no tools: one loop turn, then `final_output`. Two calls total, the
  same as the planner path costs today.
- A question with no plan at all (`has_plan=False`) — must not contradict the
  `[Plan status]` line.

## Decided

- **The answer comes from `final_output`, as a separate streamed call after the writes.**
  Not from the loop's last turn. Letting the loop answer would save one call but force
  `TOOL_SNIPPETS` into the loop's system prompt, and would require the loop to run after
  the writes in order to report them, which breaks the shape.
- **`COACH_TOOL_LOOP` guards the switch for one release.** Unlike the previous plan this is
  not additive, so the flag means keeping `build_planner_system` and `planner()` alive
  alongside the loop. That is one dead branch in `orchestrate` and code that already works,
  in exchange for being able to fall back in production by editing `.env` and restarting
  rather than reverting a branch.

## Rollout

1. Ship with `COACH_TOOL_LOOP` unset. Planner path stays live and is still the default.
2. Enable on the EC2 box. Watch `planner_decided` / `tool_call` log volume and
   `llm_call` token counts for a week or two.
3. **Delete the planner.** Remove `build_planner_system`, `TOOL_METADATA`, `planner()`,
   the flag, and the dead branch in `orchestrate`. Drop the planner-path tests.

Step 3 is a step, not an intention. A flag that never comes out means carrying two decision
mechanisms and testing both forever.
