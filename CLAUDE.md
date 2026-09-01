# Running Coach

FastAPI app that turns Garmin data into a coached training plan. Anthropic models
do the coaching, Supabase stores the data, Redis handles locks, cache and SSE fan-out.

**Read `DESIGN.md` before making architectural changes.** It is the source of truth for
how the agentic loop, plan jobs and data model work. `README.md` covers setup, env vars
and deploying to EC2. This file is only the conventions that are easy to get wrong.

## Layout

| Path | What lives there |
|---|---|
| `main.py` | App setup, request-logging middleware, orphaned lock cleanup on boot |
| `routes/` | HTTP endpoints. Thin: parse, authorize, delegate |
| `services/` | Business logic. `coach.py` and `plan.py` are the agentic loop |
| `services/prompts.py` | Every prompt string the models see |
| `db/` | Supabase and Redis access. One module per table |
| `models/` | Pydantic schemas |
| `knowledge/` | Static reference data loaded at runtime, not generated |
| `static/` | Single-page UI. `index.html` holds the theme system |

## Commands

```bash
make run      # local dev server with reload
make lint     # ruff check, no changes written
make check    # ruff + the deterministic test suite
make fix      # ruff check --fix and format
make up       # docker compose up -d
make logs     # tail all containers
```

Deploy is `git pull && docker compose up -d --build` on the EC2 box. See `README.md`.

If you change dependencies, run `make sync-requirements`. The Docker build installs
from `requirements.txt`, which is generated from `uv.lock`, so a dependency added only
to `pyproject.toml` will not exist in the container.

## Conventions

**Don't run the test suite.** Write the tests, say what to run, let the maintainer run
it. Syntax and compile checks are fine.

**Commit straight to `main`.** No feature branches unless asked.

**Coach behavior is a prompt problem.** If the coach says something wrong, phrases
things badly, or picks the wrong tool, fix the wording in `services/prompts.py`. Do not
add tool code, constants or data plumbing to work around what is really a prompt issue.

**Logging is structured JSON.** `services/logging_config.py` emits one JSON object per
line, with `request_id` and `user_id` merged in from context. Log an event name plus
`extra={...}` fields, never an f-string sentence. Never log secrets, JWTs, Garmin
credentials, health data or full prompt bodies. Logs leave the box: the `vector`
container ships them to Axiom.

**`activity_history` is multi-user.** Rows for other `user_id`s on the same dates are
normal, not a leak. Always filter by `user_id` when querying, including ad hoc queries
against production.

**Never print `.env` values.** Grep for variable names, reference them by name. This
includes keys that look public, like the Supabase anon key.

**Theme names live in three places** and must agree: the `THEMES` map in
`static/index.html`, `VALID_THEMES` in `services/write_selector.py`, and the theme lists
in `services/prompts.py`. Adding or renaming a theme means editing all three.

**The login page stays generic.** No per-user theming on `login.html`. There is no user
identity available before auth.
