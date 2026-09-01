FROM python:3.12-slim

# Python block-buffers stdout when it is a pipe rather than a terminal, so the
# JSON log lines from services/logging_config.py sat in an 8 KB buffer and never
# reached `docker logs` or the Vector shipper. uvicorn's own banner still showed
# up because it logs to stderr, which masked this.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
