import os

import redis
from dotenv import load_dotenv

load_dotenv()

_pool = redis.ConnectionPool.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)
