# Running-Coach

## Local Development

**Setup:**
1. `python3 -m venv venv`
2. `source venv/bin/activate`
3. `pip install -r requirements.txt`

**Run:**
- CLI: `python3 cli.py` (add `--debug` to see LLM + tool outputs)
- UI: `venv/bin/uvicorn main:app --reload --port 8000`

## Tests
```bash
pytest tests/test_deterministic.py
```

## Docker

**Start (background):**
```bash
docker compose up -d
```

**Rebuild after code changes:**
```bash
docker compose up -d --build
```

**Stop:**
```bash
docker compose down
```

**View logs:**
```bash
docker compose logs
docker compose logs -f   # follow live
```
