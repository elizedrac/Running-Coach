# Running-Coach

A personal AI running coach. It ingests Garmin health and activity data, stores it in Supabase, and runs an LLM tool system over it to build training plans, adjust them around illness and injury, and answer questions about training load, pacing, and race readiness. Python, FastAPI, Redis, and the Claude API.

📄 **[Design document](DESIGN.md)** — architecture and data flow, the multi-model LLM strategy and tool suite, caching, guardrails, memory compression, and error handling.

🔗 **[katerina.fit](https://katerina.fit)** — the live app. Access is invite-only for now; email elizedrac@gmail.com to request an account.

## Local Development

**Setup (venv):**
1. `python3 -m venv venv`
2. `source venv/bin/activate`
3. `pip install -r requirements.txt`

**Run (venv):**
- CLI: `python3 cli.py` (add `--debug` to see LLM + tool outputs)
- UI: `venv/bin/uvicorn main:app --reload --port 8000`

**Tests (venv):**
```bash
pytest tests/test_deterministic.py
```

OR

**Setup (uv):**
```bash
uv sync
```

**Run (uv):**
```bash
make run   # UI at http://localhost:8000
```
- CLI: `uv run python cli.py` (add `--debug` to see LLM + tool outputs)

**Tests (uv):**
```bash
make test
```

**Other make targets:**
```bash
make lint            # check for lint errors
make fix             # auto-fix lint + format
make check           # lint + test together
make lock            # update uv.lock after changing pyproject.toml
make sync-requirements  # regenerate requirements.txt from uv.lock (run before docker build if deps changed)
```

## Environment

Create a `.env` file in the project root:
```
ANTHROPIC_API_KEY=
SUPABASE_URL=
SUPABASE_KEY=          # service_role key (server-side)
SUPABASE_ANON_KEY=     # anon key, used only by /auth/login
VOYAGE_API_KEY=
WEATHER_API_KEY=
LOCATION=              # default weather location
FRONTEND_URL=
REDIS_URL=             # redis://redis:6379 in Docker; redis://localhost:6379 for host runs
USER_IDS=              # comma-separated; cron jobs + rate-limit exempt users
GARMIN_EMAIL=          # cron sync fallback credentials
GARMIN_PASSWORD=
```

## UI Themes

The web UI (`static/index.html`) ships five "Stark HUD" themes: four dark (**Arc Reactor** `arc` (default), **Jarvis** `jarvis`, **Hot Rod** `hotrod`, **Stealth** `stealth`) and one light (**Workshop** `workshop`). Switch via the theme button (bottom-right on desktop, top-right on mobile) or by asking the coach in chat ("switch to hot rod", "light mode").

How it hangs together:
- Each theme is a base palette + accent set in the `THEMES` map in `static/index.html`; all dark styling keys off `[data-theme="dark"]` and reads `--accent-rgb`, so new themes only need a palette entry.
- The choice persists per user via `POST /user/info/theme`; unknown/legacy names (e.g. pre-rework `sage`, `dark`, `hud`) fall back to `arc`.
- Chat switching goes through `services/write_selector.py` (`VALID_THEMES`) and the prompt lists in `services/prompts.py` — all three places must agree when adding or renaming a theme.

## Redis

The app needs Redis (query cache, chat history, rate limits, sync job status, and the resumable chat stream). `docker compose up` starts it automatically. For running the app directly on the host (uvicorn/CLI), start just Redis and point `REDIS_URL` at localhost:
```bash
docker compose up -d redis    # exposed on localhost:6379
```
Tests do NOT need Redis, they run against fakeredis.

Chat answers are generated in a background task and streamed through a Redis Stream, not tied to the HTTP request, so a page reload reattaches to an in-flight answer instead of killing it. `POST /ask` starts the job; `GET /ask/stream/{session_id}` follows it; `POST /ask/stop/{session_id}` cancels. A garmin sync inside a chat turn emits per-day progress into the stream, which both shows "day X of Y" and keeps the connection from tripping the orphan guard during a long sync. Chat-triggered syncs share the same Redis lock, status key, and cancel flag as the sync button (`POST /garmin-sync`), so only one sync can run per user at a time and a chat sync can be cancelled from either the chat Stop button or the sync popover's Cancel.

Plan creation and plan sync follow the same shape, minus the streaming: `POST /plan/create` and `POST /plan/sync` acquire a Redis lock, start a background job, and return `{"status": "started"}` immediately, and the frontend polls `GET /plan/job/status` until the job publishes a terminal blob. Neither has token output to stream, so they use status polling rather than a Redis Stream. A second concurrent job gets a 409. The coach's `update_plan` tool shares that lock too (`run_locked_plan_update`), so a Sync Plan click and a chat-driven plan change can never write the same days at once. Chat-initiated updates run inline, since the chat turn is already a background job.

Every lock is released in a `finally`, which cannot run if the process dies, so a restart or OOM used to leave the key behind for its full 40 minute TTL and refuse every plan update, sync or chat turn with nothing actually running (Redis keeps an anonymous volume at `/data`, so its snapshot carries the stale keys across a container recreate). A `lifespan` hook clears `plan_job_lock`, `garmin_sync_lock`, `chatlock` and `chatcancel` on startup, when no job can be in flight. `chatstream` is left alone, since a page reload reattaches to it.

Every plan write snapshots the days it is about to touch (full rows plus interval breakdowns) into a Redis undo stack 3 deep, so `POST /plan/undo` and `POST /plan/redo` restore real state rather than asking the model to reconstruct it from a note. A write pushes to undo and clears redo, the way an editor greys out the redo arrow once you type something new. Both restores take the plan lock and run synchronously; `GET /plan/undo/status` gives the UI the two depths.

`update_plan` reports `success`, `partial`, `fail`, `no_changes` (the plan already matched) or `out_of_window` (the dates are outside the writable range, which is today−7 → today+8 in chat and this week's Monday → today for a sync). Changes are validated one at a time so a single malformed day cannot discard the rest, and every model reply is parsed by `extract_json()` rather than by slicing first-brace-to-last-brace.

## Docker

**Build:**
```bash
make build
# or: docker compose build
```

**Start (background):**
```bash
make up
# or: docker compose up -d
```

**Rebuild after code changes:**
```bash
make build && make up
# or: docker compose up -d --build
```

**Stop:**
```bash
make down
# or: docker compose down
```

**View logs:**
```bash
make logs
# or: docker compose logs -f
```

---

## EC2 (Production)

**Instance:** t3.small, Ubuntu 24.04 LTS, IP: `18.222.142.90`
**Access:** `http://18.222.142.90`

### Deploying updates

SSH in:
```bash
ssh -i ~/.ssh/run-key.pem ubuntu@18.222.142.90
```

Pull and rebuild:
```bash
cd Running-Coach
git pull
docker compose up -d --build
```

Or all in one line:
```bash
ssh -i ~/.ssh/run-key.pem ubuntu@18.222.142.90 "cd Running-Coach && git pull && docker compose up -d --build"
```

### View logs
```bash
cd Running-Coach
docker compose logs -f
```

### Restart app
```bash
cd Running-Coach
docker compose restart
```

### nginx

Config lives at `/etc/nginx/sites-available/runcoach`. After any nginx config change:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

### Adding a domain + HTTPS (when ready)

1. Buy a domain, add an **A record** pointing to `18.222.142.90`
2. Wait for DNS to propagate
3. SSH in and run:
```bash
sudo certbot --nginx -d yourdomain.com
```
Certbot auto-configures nginx for HTTPS and sets up auto-renewal.

4. Update `/etc/nginx/sites-available/runcoach` to add `server_name yourdomain.com;` and the HTTPS redirect block.
