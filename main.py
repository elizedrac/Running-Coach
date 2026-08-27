# FastAPI app entry point (Phase 4). Registers routes/.
import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routes.activities import router as data_router
from routes.ask import router as ask_router
from routes.auth import router as auth_router
from routes.plan import router as plan_router
from routes.user import router as user_router
from services.logging_config import clear_log_context, get_logger, init_log_context, setup_logging

setup_logging()
logger = get_logger(__name__)

app = FastAPI()

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
