# CLI entry point (V1). Thin wrapper that calls services/coach.py::ask().
from db.client import get_supabase_client
from services.garmin import _fetch_garmin_data

def main():
    # supabase = get_supabase_client()
    # test = supabase.from_("users").select("*").execute()
    # print(test)

    activities = _fetch_garmin_data()
    print(activities)

if __name__ == "__main__":
    main()