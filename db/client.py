# Supabase client factory. create_client() imported everywhere that touches the DB.
import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

def get_supabase_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    return create_client(url, key)