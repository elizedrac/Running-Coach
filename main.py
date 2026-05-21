# FastAPI app entry point (Phase 4). Registers routes/.
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from routes.ask import router as ask_router
from routes.activities import router as data_router
from routes.plan import router as plan_router
import os

app = FastAPI()

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
os.environ["SERVER_MODE"] = "1"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ask_router)
app.include_router(data_router)
app.include_router(plan_router)

app.mount("/", StaticFiles(directory="static", html=True), name="static")