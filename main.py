# FastAPI app entry point (Phase 4). Registers routes/.
import json
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from db.redis import get_redis
from routes.activities import router as data_router
from routes.ask import router as ask_router
from routes.auth import router as auth_router
from routes.plan import router as plan_router
from routes.user import router as user_router
from services.logging_config import clear_log_context, get_logger, init_log_context, setup_logging

setup_logging()
logger = get_logger(__name__)


def clear_orphaned_job_locks():
    """Every job lock is released in a `finally`, which cannot run if the process
    dies mid-job — a restart or OOM left the key behind for the full 40 min TTL and
    every plan update, sync or chat turn was refused with nothing actually running.
    Redis keeps an anonymous volume at /data, so its snapshot brings the stale keys
    back across a container recreate rather than losing them.

    Background jobs die with the process, so on boot no job can be in flight and any
    surviving lock is stale by definition. Safe while one app container runs; with
    two, a restart of one would clear the other's live locks, and the fix then is a
    heartbeat that re-stamps a short TTL while the job runs.
    """
    redis = get_redis()
    # chatcancel goes too: a stale flag would cancel the next turn on that session
    # the instant it started, since the session id survives in the browser's storage.
    # chatstream is deliberately left alone — it is replayable content, not a lock.
    patterns = ("plan_job_lock:*", "garmin_sync_lock:*", "chatlock:*", "chatcancel:*")
    cleared = [k for pattern in patterns for k in redis.scan_iter(pattern)]
    for key in cleared:
        redis.delete(key)
    if cleared:
        logger.warning("cleared_orphaned_locks", extra={"count": len(cleared)})
    _resolve_orphaned_job_status(redis)


def _resolve_orphaned_job_status(redis) -> None:
    """A job's status blob outlives its lock: the lock expires in 40 min, the blob in
    a day. Clearing only the lock still left `{"status": "running"}` readable for 24 h,
    so a page that reattached to it polled a job that had died with the old process
    and span until the tab was closed.

    The two blobs are resolved differently because their pollers read a missing key
    differently. pollSyncStatus treats "idle" as finished and stops cleanly, so the
    garmin blob is just deleted. pollPlanJob passes whatever it reads straight to
    planCreateDone/planSyncDone, which word their toast off the status, so the plan
    blob is rewritten to an error instead of dropping to a silent "idle". `kind` is
    preserved because those two handlers are selected by it.

    Per key rather than all-or-nothing: this runs inside `lifespan`, so an unparseable
    value under either prefix would otherwise stop the app booting at all. A blob we
    cannot read is not worth refusing to start over.
    """
    stale = 0
    for key in redis.scan_iter("garmin_sync_status:*"):
        try:
            raw = redis.get(key)
            if raw and json.loads(raw).get("status") == "running":
                redis.delete(key)
                stale += 1
        except Exception:
            logger.warning("orphaned_status_unreadable", extra={"key": str(key)}, exc_info=True)
    for key in redis.scan_iter("plan_job_status:*"):
        try:
            raw = redis.get(key)
            if not raw:
                continue
            blob = json.loads(raw)
            if blob.get("status") != "running":
                continue
            blob.update({"status": "error", "error": "Interrupted by a server restart. Please try again."})
            # keepttl: the blob is already counting down from when the job started, and
            # a fresh 24 h would leave the "didn't finish" notice up a day past its time.
            redis.set(key, json.dumps(blob), keepttl=True)
            stale += 1
        except Exception:
            logger.warning("orphaned_status_unreadable", extra={"key": str(key)}, exc_info=True)
    if stale:
        logger.warning("resolved_orphaned_job_status", extra={"count": stale})


@asynccontextmanager
async def lifespan(app: FastAPI):
    clear_orphaned_job_locks()
    yield


app = FastAPI(lifespan=lifespan)

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
os.environ["SERVER_MODE"] = "1"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    # Static asset noise would drown out the app's own events.
    if request.url.path.startswith("/static") or request.url.path in ("/", "/login", "/favicon.ico"):
        return await call_next(request)
    # Fresh dict per request; get_current_user adds user_id into this same dict.
    init_log_context(request_id=uuid.uuid4().hex[:12])
    started = time.monotonic()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "duration_ms": round((time.monotonic() - started) * 1000),
            },
        )
        clear_log_context()


app.include_router(auth_router)
app.include_router(ask_router)
app.include_router(data_router)
app.include_router(plan_router)
app.include_router(user_router)


@app.get("/login")
def login_page():
    return FileResponse("static/login.html")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
