# Running-Coach

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

## Redis

The app needs Redis (query cache, chat history, rate limits, sync job status, and the resumable chat stream). `docker compose up` starts it automatically. For running the app directly on the host (uvicorn/CLI), start just Redis and point `REDIS_URL` at localhost:
```bash
docker compose up -d redis    # exposed on localhost:6379
```
Tests do NOT need Redis, they run against fakeredis.

Chat answers are generated in a background task and streamed through a Redis Stream, not tied to the HTTP request, so a page reload reattaches to an in-flight answer instead of killing it. `POST /ask` starts the job; `GET /ask/stream/{session_id}` follows it; `POST /ask/stop/{session_id}` cancels. A garmin sync inside a chat turn emits per-day progress into the stream, which both shows "day X of Y" and keeps the connection from tripping the orphan guard during a long sync.

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
