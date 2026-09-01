from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from db.plan import (
    clear_day,
    day_belongs_to,
    delete_plan,
    get_plan_days,
    get_plan_id,
    get_plan_intervals,
    patch_plan,
)
from db.preferences import (
    get_preferences,
    set_avg_miles,
    set_days_per_week,
    set_max_miles,
    set_notes,
    set_preferred_days,
    set_time_based,
)
from db.race import get_race, set_goal_time, set_race_date, set_race_distance, set_race_type
from models.planner import PatchDayRequest, PreferencesRequest, RaceRequest, SyncPlanRequest
from services.auth import get_current_user
from services.logging_config import get_logger
from services.plan import (
    acquire_plan_lock,
    get_plan_job_status,
    get_undo_depths,
    mark_plan_job_running,
    record_day_undo,
    redo_plan,
    run_plan_job,
    undo_plan,
)
from services.rate_limit import check_rate_limit

logger = get_logger(__name__)

router = APIRouter()


# race routers
@router.get("/race")
def get_race_data(user_id: str = Depends(get_current_user)):
    try:
        return get_race(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/race/description")
def race_description(body: RaceRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_race_type(user_id, body.race_description)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/race/time")
def race_time(body: RaceRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_goal_time(user_id, body.goal_time)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/race/distance")
def race_miles(body: RaceRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_race_distance(user_id, body.race_distance_miles)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/race/date")
def race_date(body: RaceRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_race_date(user_id, body.race_date)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# preferences routers
@router.get("/preferences")
def get_preference_data(user_id: str = Depends(get_current_user)):
    try:
        return get_preferences(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preferences/total-days")
def total_days(body: PreferencesRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_days_per_week(user_id, body.days_per_week)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preferences/preferred-days")
def preferred_days(body: PreferencesRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_preferred_days(user_id, body.preferred_days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preferences/avg-miles")
def avg_miles(body: PreferencesRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_avg_miles(user_id, body.avg_miles)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preferences/max-miles")
def max_miles(body: PreferencesRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_max_miles(user_id, body.max_miles)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preferences/time-based")
def time_based(body: PreferencesRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_time_based(user_id, body.time_based)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preferences/notes")
def notes(body: PreferencesRequest, user_id: str = Depends(get_current_user)):
    try:
        return set_notes(user_id, body.notes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# plan routers
@router.post("/plan/create")
def new_plan(background_tasks: BackgroundTasks, user_id: str = Depends(get_current_user)):
    check_rate_limit(user_id, limit=5, window=3600)
    if not acquire_plan_lock(user_id):
        raise HTTPException(status_code=409, detail="A plan job is already running.")
    mark_plan_job_running(user_id, "create")
    background_tasks.add_task(run_plan_job, user_id, "create")
    return {"status": "started", "kind": "create"}


@router.get("/plan/undo/status")
def undo_status(user_id: str = Depends(get_current_user)):
    """Drives whether the UI shows the buttons at all."""
    return get_undo_depths(user_id)


@router.post("/plan/undo")
def undo(user_id: str = Depends(get_current_user)):
    # Synchronous: this is a handful of row writes against a two-week window, nothing
    # like the agent loop /plan/create runs. It still takes the plan lock so it cannot
    # interleave with a sync.
    return undo_plan(user_id)


@router.post("/plan/redo")
def redo(user_id: str = Depends(get_current_user)):
    return redo_plan(user_id)


@router.get("/plan/job/status")
def plan_job_status(user_id: str = Depends(get_current_user)):
    return get_plan_job_status(user_id)


@router.get("/plan/days")
def get_plan_days_route(
    start_date: str = None, end_date: str = None, week_number: int = None, user_id: str = Depends(get_current_user)
):
    start = start_date or (date.today() - timedelta(days=7)).isoformat()
    end = end_date or date.today().isoformat()
    try:
        plan_id = get_plan_id(user_id)
        return get_plan_days(plan_id, start_date=start, end_date=end, week_number=week_number)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plan/intervals/{day_id}")
def get_intervals(day_id: str, user_id: str = Depends(get_current_user)):
    try:
        return get_plan_intervals(day_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/plan/delete")
def remove_plan(user_id: str = Depends(get_current_user)):
    try:
        return delete_plan(user_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/plan/sync")
def sync_plan(
    background_tasks: BackgroundTasks,
    body: SyncPlanRequest = SyncPlanRequest(),
    user_id: str = Depends(get_current_user),
):
    check_rate_limit(user_id, limit=10, window=3600)
    today = body.today or date.today().isoformat()
    try:
        # Parsed purely to validate: a malformed date must 400 here rather than 500 deeper in.
        date.fromisoformat(today)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid 'today' date.")
    intent = f"Reconcile plan with actual Garmin activities. Today is {today}. Update completed days (start of current week through today) to reflect actual Garmin activities. Do NOT change any day after {today} — reconciliation reports what happened, it does not re-plan what has not happened yet."
    if not acquire_plan_lock(user_id):
        raise HTTPException(status_code=409, detail="A plan job is already running.")
    mark_plan_job_running(user_id, "sync")
    background_tasks.add_task(
        run_plan_job,
        user_id,
        "sync",
        intent=intent,
        include_activities=True,
        local_today=today,
        mode="sync",
        allowed_end=today,
    )
    return {"status": "started", "kind": "sync"}


def _owned_day(user_id: str, day_id: str) -> str:
    """These routes address a plan day by raw uuid, so without this any authenticated
    user could edit or clear another user's day by supplying its id. Returns the
    caller's plan_id, which the undo snapshot then reuses.

    404 rather than 403: a caller who does not own the day should not learn it exists.
    """
    plan_id = get_plan_id(user_id)
    if not plan_id or not day_belongs_to(plan_id, day_id):
        raise HTTPException(status_code=404, detail="Day not found.")
    return plan_id


@router.patch("/plan/day/{day_id}")
def patch_day(day_id: str, body: PatchDayRequest, user_id: str = Depends(get_current_user)):
    # Outside the try: its 404 must reach the client as a 404, not be reworded as a 500.
    plan_id = _owned_day(user_id, day_id)
    try:
        record_day_undo(user_id, day_id, plan_id=plan_id)
        # exclude_unset keeps "field omitted" distinct from "field explicitly null" —
        # without it a cleared field looks identical to an untouched one and can never
        # be written back as empty.
        return patch_plan(day_id, body.model_dump(exclude_unset=True))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/plan/day/{day_id}")
def delete_day(day_id: str, user_id: str = Depends(get_current_user)):
    plan_id = _owned_day(user_id, day_id)
    try:
        record_day_undo(user_id, day_id, plan_id=plan_id)
        return clear_day(day_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
