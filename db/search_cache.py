from datetime import datetime, timedelta, timezone

from db.client import get_supabase_client

TTL_DAYS = {
    "race_info": 365,
}


def get_raw_search(race: str, location: str, info_type: str) -> str | None:
    """Fetch the most recent non-expired raw_result cached on any race_info row for this race/location/info_type."""
    client = get_supabase_client()
    try:
        now = datetime.now(timezone.utc).isoformat()
        response = (
            client.table("search_cache")
            .select("raw_result")
            .eq("topic", "race_info")
            .eq("race", race)
            .eq("location", location)
            .eq("info_type", info_type)
            .not_.is_("raw_result", "null")
            .gt("expires_at", now)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return response.data[0]["raw_result"] if response.data else None
    except Exception as e:
        print(f"Error reading raw search cache: {e}")
        return None


def get_candidates(topic: str, race: str, location: str, info_type: str) -> list[dict]:
    """Fetch all non-expired cached rows for this topic/race/location/info_type partition."""
    client = get_supabase_client()
    try:
        now = datetime.now(timezone.utc).isoformat()
        response = (
            client.table("search_cache")
            .select("*")
            .eq("topic", topic)
            .eq("race", race)
            .eq("location", location)
            .eq("info_type", info_type)
            .gt("expires_at", now)
            .execute()
        )
        return response.data
    except Exception as e:
        print(f"Error reading search cache: {e}")
        return []


def set_cached(
    query: str,
    result: str,
    race: str,
    location: str,
    info_type: str,
    embedding: list[float] | None = None,
    topic: str = "race_info",
    source: str = "web_search",
    raw_result: str | None = None,
) -> None:
    client = get_supabase_client()
    expires_at = (datetime.now(timezone.utc) + timedelta(days=TTL_DAYS.get(topic, 7))).isoformat()
    try:
        client.table("search_cache").insert(
            {
                "query": query,
                "result": result,
                "topic": topic,
                "race": race,
                "location": location,
                "info_type": info_type,
                "source": source,
                "expires_at": expires_at,
                "embedding": embedding,
                "raw_result": raw_result,
            }
        ).execute()
    except Exception as e:
        print(f"Error writing search cache: {e}")
