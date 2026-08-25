# Read/write queries for current_plan, plan_days, plan_intervals, plan_history.
import json
from datetime import date

from db.client import get_supabase_client
from db.race import get_race


def get_current_plan(user_id) -> dict:
    client = get_supabase_client()
    try:
        response = client.table("current_plan").select("*").eq("user_id", user_id).execute()
        return response.data[0] if response.data else {}
    except Exception:
        return {}


def get_plan_id(user_id):
    return get_current_plan(user_id).get("id")


def get_plan_days(plan_id, week_number=None, start_date=None, end_date=None) -> list:
    client = get_supabase_client()
    try:
        if week_number:
            response = (
                client.table("plan_days").select("*").eq("plan_id", plan_id).eq("week_number", week_number).execute()
            )
            return response.data
        elif start_date and end_date:
            response = (
                client.table("plan_days")
                .select("*")
                .eq("plan_id", plan_id)
                .gte("plan_date", start_date)
                .lte("plan_date", end_date)
                .execute()
            )
            return response.data
        return []
    except Exception:
        return []


def get_day_id(plan_id, day):
    results = get_plan_days(plan_id, start_date=day, end_date=day)
    return results[0]["id"] if results else None


def get_plan_intervals(day_id):
    client = get_supabase_client()
    try:
        response = client.table("plan_intervals").select("*").eq("day_id", day_id).execute()
        return response.data
    except Exception:
        return []


def save_plan_intervals(day_id, intervals: list) -> dict:
    # No intervals is a valid end state (the caller cleared them), not a failure.
    if not intervals:
        return {"status": "success"}
    client = get_supabase_client()
    try:
        rows = [{"day_id": day_id, **interval} for interval in intervals]
        client.table("plan_intervals").insert(rows).execute()
        return {"status": "success"}
    except Exception as e:
        return {"status": "fail", "error": str(e)}


def save_plan_day(plan_id, days: list) -> list:
    client = get_supabase_client()
    try:
        rows = [{"plan_id": plan_id, **day} for day in days]
        response = client.table("plan_days").insert(rows).execute()
        return response.data
    except Exception:
        return []


def save_plan(user_id, days: list):
    client = get_supabase_client()
    race = get_race(user_id)

    name = race.get("race_type")
    race_date = race.get("race_date")
    time = race.get("goal_time")
    elapsed = date.fromisoformat(race.get("race_date")[:10]) - date.today()
    weeks = elapsed.days // 7

    try:
        response = (
            client.table("current_plan")
            .upsert(
                {
                    "user_id": user_id,
                    "race_name": name,
                    "race_date": race_date,
                    "goal_time": time,
                    "total_weeks": weeks,
                },
                on_conflict="user_id",
            )
            .execute()
        )

        plan_id = response.data[0]["id"]
        day_rows = [{k: v for k, v in day.items() if k != "intervals"} for day in days]
        response_days = save_plan_day(plan_id, day_rows)

        for og_day, in_day in zip(days, response_days):
            intervals = og_day.get("intervals", [])
            if intervals:
                save_plan_intervals(in_day["id"], intervals)

        return {"status": "success"}
    except Exception as e:
        print(f"[save_plan] ERROR: {e}")
        import traceback

        traceback.print_exc()
        return {"status": "fail"}


def delete_plan(user_id) -> dict:
    client = get_supabase_client()
    try:
        client.table("current_plan").delete().eq("user_id", user_id).execute()
        return {"status": "success"}
    except Exception:
        return {"status": "fail"}


def update_plan_day(plan_id: int, changes: list) -> dict:
    """Apply changes per-day. One failing change no longer aborts the rest;
    returns applied/failed lists so callers report only what actually happened."""
    client = get_supabase_client()
    applied, failed, interval_failures = [], [], []
    for change in changes:
        try:
            day_id = get_day_id(plan_id, change["plan_date"])
            data = {k: v for k, v in change.items() if k != "intervals"}
            if data.get("workout_type") in {"REST", "CROSS", "STRENGTH"}:
                data["target_pace"] = None
                data["target_miles"] = None
            if day_id and data.get("workout_type"):
                current = get_plan_days(plan_id, start_date=change["plan_date"], end_date=change["plan_date"])
                if current:
                    orig = current[0]
                    orig_type = orig.get("workout_type", "")
                    existing_notes = data.get("notes") or ""
                    if orig_type and orig_type != data.get("workout_type") and not existing_notes.startswith("Was:"):
                        parts = [f"Was: {orig_type}"]
                        if orig.get("target_miles"):
                            parts.append(f"{orig['target_miles']}mi")
                        if orig.get("target_pace"):
                            parts.append(f"@ {orig['target_pace']}")
                        if orig_type == "INTERVAL":
                            orig_intervals = get_plan_intervals(day_id)
                            if orig_intervals:
                                parts.append(f"| intervals: {json.dumps(orig_intervals)}")
                        orig_summary = " ".join(parts)
                        data["notes"] = f"{orig_summary}\n{existing_notes}".strip() if existing_notes else orig_summary
            if not day_id:
                result = save_plan_day(plan_id, [data])
                day_id = result[0]["id"]
            else:
                client.table("plan_days").update(data).eq("id", day_id).execute()
            # Only touch intervals when the change actually supplies them — an omitted
            # field must leave the day's existing interval breakdown intact
            if change.get("workout_type") == "INTERVAL" and change.get("intervals"):
                # Best effort: a failed interval write must NOT fail the day change.
                # The workout type and notes still describe the session, so the athlete
                # gets a usable day. Surfaced in interval_failures for the caller to log.
                interval_result = replace_plan_intervals(day_id, change["intervals"])
                if interval_result.get("status") != "success":
                    interval_failures.append(
                        {"plan_date": change["plan_date"], "error": interval_result.get("error")}
                    )
            elif change.get("workout_type") and change.get("workout_type") != "INTERVAL":
                # Type changed away from INTERVAL — drop the orphaned reps so switching
                # back later doesn't resurrect a breakdown for a workout that's gone
                replace_plan_intervals(day_id, [])
            applied.append(change)
        except Exception as e:
            failed.append({"change": change, "error": str(e)})
    if failed and applied:
        status = "partial"
    elif failed:
        status = "fail"
    else:
        status = "success"
    return {"status": status, "applied": applied, "failed": failed, "interval_failures": interval_failures}


def replace_plan_intervals(day_id: int, intervals) -> dict:
    """Always returns a status dict. Previously returned None on the success path,
    which made a failed insert indistinguishable from a successful one."""
    client = get_supabase_client()
    try:
        client.table("plan_intervals").delete().eq("day_id", day_id).execute()
    except Exception as e:
        return {"status": "fail", "error": str(e)}
    return save_plan_intervals(day_id, intervals)


def patch_plan(day_id: str, changes: dict) -> dict:
    client = get_supabase_client()
    # Whatever the caller sends is written verbatim, nulls included — that is how a
    # field gets cleared. The route uses exclude_unset so untouched fields never
    # arrive here in the first place.
    data = {k: v for k, v in changes.items() if k != "intervals"}
    # A day must always have a workout type, so never null that one out
    if data.get("workout_type") is None:
        data.pop("workout_type", None)
    if not data:
        return {"status": "no changes"}
    try:
        client.table("plan_days").update(data).eq("id", day_id).execute()
    except Exception as e:
        return {"status": f"fail with error{e}"}

    # Intervals are best effort and run AFTER the day is already saved — a failure
    # here must not report the whole patch as failed, or the UI shows "failed" for
    # a day that did save. Returned alongside the success so the caller can say so.
    interval_result = None
    if changes.get("intervals") is not None:
        interval_result = replace_plan_intervals(day_id, changes["intervals"])
    elif data.get("workout_type") and data["workout_type"] != "INTERVAL":
        # Left INTERVAL — drop orphaned reps so switching back doesn't resurrect them
        interval_result = replace_plan_intervals(day_id, [])

    if interval_result and interval_result.get("status") != "success":
        return {"status": "success", "intervals": interval_result}
    return {"status": "success"}


def clear_day(day_id: str) -> dict:
    client = get_supabase_client()
    try:
        client.table("plan_days").update(
            {"workout_type": "REST", "target_miles": None, "target_pace": None, "notes": None}
        ).eq("id", day_id).execute()
        return {"status": "success"}
    except Exception as e:
        return {"status": f"fail with error{e}"}
