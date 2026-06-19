# Supabase client singleton. Instantiated once at import time.
import os

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

_client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


def get_supabase_client():
    return _client
