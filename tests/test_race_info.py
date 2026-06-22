# Tests for the race_info feature: db/search_cache.py CRUD + services/race_info.py similarity caching.
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from db.search_cache import get_candidates, set_cached
from models.planner import RaceDayInfo, RaceRegistrationInfo
from services.race_info import INFO_MODELS, _cosine, _find_cached, _word_overlap, get_race_info

# ── db/search_cache.py: get_candidates ───────────────────────────────────────


@patch("db.search_cache.get_supabase_client")
def test_get_candidates_filters_by_topic_race_location_info_type(mock_client):
    chain = mock_client.return_value
    eq_chain = chain.table.return_value.select.return_value.eq
    eq_chain.return_value.eq.return_value.eq.return_value.eq.return_value.gt.return_value.execute.return_value.data = [
        {"query": "registration dates", "result": "{}"}
    ]
    result = get_candidates("race_info", "marathon", "Chicago", "registration")
    assert len(result) == 1
    eq_chain.assert_any_call("topic", "race_info")
    eq_chain.return_value.eq.assert_any_call("race", "marathon")


@patch("db.search_cache.get_supabase_client")
def test_get_candidates_db_error_returns_empty_list(mock_client):
    mock_client.return_value.table.side_effect = Exception("db down")
    result = get_candidates("race_info", "marathon", "Chicago", "registration")
    assert result == []


# ── db/search_cache.py: set_cached ───────────────────────────────────────────


@patch("db.search_cache.get_supabase_client")
def test_set_cached_inserts_row_with_expiry(mock_client):
    chain = mock_client.return_value
    chain.table.return_value.insert.return_value.execute.return_value = MagicMock()
    set_cached("registration dates", "{}", "marathon", "Chicago", "registration", embedding=[0.1, 0.2])
    inserted = chain.table.return_value.insert.call_args[0][0]
    assert inserted["race"] == "marathon"
    assert inserted["location"] == "Chicago"
    assert inserted["info_type"] == "registration"
    assert inserted["embedding"] == [0.1, 0.2]
    expires_at = datetime.fromisoformat(inserted["expires_at"])
    assert expires_at > datetime.now(timezone.utc) + timedelta(days=300)  # race_info TTL is 365 days


@patch("db.search_cache.get_supabase_client")
def test_set_cached_db_error_does_not_raise(mock_client):
    mock_client.return_value.table.side_effect = Exception("db down")
    set_cached("query", "result", "marathon", "Chicago", "registration")  # should not raise


# ── services/race_info.py: similarity helpers ────────────────────────────────


def test_word_overlap_near_identical_phrasing():
    assert _word_overlap("qualifying standards for 2026", "qualifying standards 2026") >= 0.7


def test_word_overlap_unrelated_phrasing_low_score():
    assert _word_overlap("qualifying standards for 2026", "what time does the race start") < 0.3


def test_cosine_identical_vectors():
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == 1.0


def test_cosine_orthogonal_vectors():
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


# ── services/race_info.py: _find_cached ──────────────────────────────────────


@patch("services.race_info.get_candidates")
def test_find_cached_word_overlap_hit_skips_embedding(mock_candidates):
    mock_candidates.return_value = [{"query": "qualifying standards 2026", "result": "cached-result", "embedding": None}]
    result = _find_cached("marathon", "Chicago", "registration", "qualifying standards for 2026")
    assert result == "cached-result"


@patch("services.race_info.get_candidates", return_value=[])
def test_find_cached_no_candidates_returns_none(mock_candidates):
    assert _find_cached("marathon", "Chicago", "registration", "anything") is None


@patch("services.race_info.voyage_client")
@patch("services.race_info.get_candidates")
def test_find_cached_embedding_similarity_above_threshold(mock_candidates, mock_voyage):
    mock_candidates.return_value = [
        {"query": "totally different phrasing", "result": "cached-result", "embedding": [1.0, 0.0]}
    ]
    mock_voyage.embed.return_value.embeddings = [[1.0, 0.0]]
    result = _find_cached("marathon", "Chicago", "registration", "another way to ask this")
    assert result == "cached-result"


@patch("services.race_info.voyage_client")
@patch("services.race_info.get_candidates")
def test_find_cached_embedding_similarity_below_threshold_returns_none(mock_candidates, mock_voyage):
    mock_candidates.return_value = [
        {"query": "totally different phrasing", "result": "cached-result", "embedding": [0.0, 1.0]}
    ]
    mock_voyage.embed.return_value.embeddings = [[1.0, 0.0]]  # orthogonal → similarity 0
    result = _find_cached("marathon", "Chicago", "registration", "another way to ask this")
    assert result is None


@patch("services.race_info.voyage_client", None)
@patch("services.race_info.get_candidates")
def test_find_cached_no_voyage_client_returns_none_on_overlap_miss(mock_candidates):
    mock_candidates.return_value = [{"query": "totally different phrasing", "result": "x", "embedding": [1.0, 0.0]}]
    result = _find_cached("marathon", "Chicago", "registration", "another way to ask this")
    assert result is None


# ── services/race_info.py: get_race_info ─────────────────────────────────────


@patch("services.race_info.web_search")
@patch("services.race_info._find_cached")
def test_get_race_info_cache_hit_skips_web_search(mock_find_cached, mock_web_search):
    cached_payload = json.dumps(
        {"info_type": "registration", "race": "marathon", "location": "Chicago", "info": {"registration_timeline": "Opens Jan 2026"}}
    )
    mock_find_cached.return_value = cached_payload
    result = get_race_info("user1", "marathon", "Chicago", "registration", "registration dates")
    mock_web_search.assert_not_called()
    assert result["info"]["registration_timeline"] == "Opens Jan 2026"


@patch("services.race_info.voyage_client")
@patch("services.race_info.set_cached")
@patch("services.race_info.call_llm")
@patch("services.race_info.web_search", return_value="search text")
@patch("services.race_info._find_cached", return_value=None)
def test_get_race_info_cache_miss_runs_full_flow(
    mock_find_cached, mock_web_search, mock_call_llm, mock_set_cached, mock_voyage
):
    mock_voyage.embed.return_value.embeddings = [[0.1, 0.2]]
    mock_call_llm.return_value = json.dumps(
        {
            "info_type": "registration",
            "race": "marathon",
            "location": "Chicago",
            "info": {
                "registration_timeline": "Opens March 2026",
                "qualifying_times": None,
                "registration_methods": None,
                "registration_costs": None,
                "corral_details": None,
                "additional_details": None,
            },
        }
    )
    result = get_race_info("user1", "marathon", "Chicago", "registration", "registration dates")
    mock_web_search.assert_called_once()
    mock_set_cached.assert_called_once()
    assert result["info"]["registration_timeline"] == "Opens March 2026"


@patch("services.race_info.voyage_client")
@patch("services.race_info.set_cached")
@patch("services.race_info.call_llm")
@patch("services.race_info.web_search", return_value="search text")
@patch("services.race_info._find_cached", return_value=None)
def test_get_race_info_race_day_union_resolved_correctly(
    mock_find_cached, mock_web_search, mock_call_llm, mock_set_cached, mock_voyage
):
    # Regression test: info_type="race_day" must produce a RaceDayInfo, not silently
    # collapse into RaceRegistrationInfo (a plain Union always matches the first member
    # since both submodels have all-optional fields).
    mock_voyage.embed.return_value.embeddings = [[0.1, 0.2]]
    mock_call_llm.return_value = json.dumps(
        {
            "info_type": "race_day",
            "race": "marathon",
            "location": "Chicago",
            "info": {
                "start_time": "8:00 AM",
                "start_location": "Grant Park",
                "corral_details": None,
                "additional_details": None,
            },
        }
    )
    result = get_race_info("user1", "marathon", "Chicago", "race_day", "what time does the race start")
    assert result["info"]["start_time"] == "8:00 AM"
    assert result["info"]["start_location"] == "Grant Park"


def test_info_models_mapping():
    assert INFO_MODELS["registration"] is RaceRegistrationInfo
    assert INFO_MODELS["race_day"] is RaceDayInfo
