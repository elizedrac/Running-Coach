# CLI entry point (V1). Thin wrapper that calls services/coach.py::ask().
from db.client import get_supabase_client
from services.garmin import garmin_sync

def main():
    # supabase = get_supabase_client()
    # test = supabase.from_("users").select("*").execute()
    # print(test)

    id = "f02a4ceb-0549-4816-ac88-07001be65c70"

    garmin_sync(id, "2026-01-08", "2026-01-09")


if __name__ == "__main__":
    main()