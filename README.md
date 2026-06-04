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

---

## EC2 (Production)

**Instance:** t3.small, Ubuntu 26.04 LTS, IP: `18.222.142.90`
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

Deploying updates all-together:
ssh -i ~/.ssh/run-key.pem ubuntu@18.222.142.90
cd Running-Coach && git pull && docker compose up -d --build

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
