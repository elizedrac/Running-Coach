import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import fakeredis
import pytest
import redis

import db.redis


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    # Fresh in-memory server per test: no real Redis needed, no state bleed between tests
    server = fakeredis.FakeServer()
    pool = redis.ConnectionPool(connection_class=fakeredis.FakeRedisConnection, server=server)
    monkeypatch.setattr(db.redis, "_pool", pool)
