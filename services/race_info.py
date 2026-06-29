import json
import os
import re

import voyageai
from dotenv import load_dotenv

from db.search_cache import get_candidates, get_raw_search, set_cached
from models.planner import RaceDayInfo, RaceInfoPlan, RaceRegistrationInfo
from services.llm import call_llm
from services.prompts import RACE_INFO_SYSTEM
from services.web_search import web_search

load_dotenv()

INFO_MODELS = {"registration": RaceRegistrationInfo, "race_day": RaceDayInfo}

WORD_OVERLAP_THRESHOLD = 0.7
EMBEDDING_SIMILARITY_THRESHOLD = 0.7

VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY) if VOYAGE_API_KEY else None


def _word_overlap(a: str, b: str) -> float:
    wa = set(re.sub(r"[^a-z0-9 ]", "", a.lower()).split())
    wb = set(re.sub(r"[^a-z0-9 ]", "", b.lower()).split())
    union = wa | wb
    return len(wa & wb) / len(union) if union else 0.0


def _cosine(vec1, vec2) -> float:
    return sum(a * b for a, b in zip(vec1, vec2))


def _find_cached(race: str, location: str, info_type: str, query: str) -> str | None:
    candidates = get_candidates("race_info", race, location, info_type)
    if not candidates:
        return None

    for row in candidates:
        if _word_overlap(query, row.get("query", "")) >= WORD_OVERLAP_THRESHOLD:
            return row["result"]

    if not voyage_client:
        return None

    embedded_query = voyage_client.embed([query], model="voyage-3-lite").embeddings[0]
    scored = [(row, _cosine(embedded_query, row["embedding"])) for row in candidates if row.get("embedding")]
    if not scored:
        return None

    best_row, best_score = max(scored, key=lambda pair: pair[1])
    return best_row["result"] if best_score >= EMBEDDING_SIMILARITY_THRESHOLD else None


def get_race_info(user_id: str, race: str, location: str, info_type: str, query: str) -> dict:
    cached = _find_cached(race, location, info_type, query)
    if cached:
        return json.loads(cached)

    results = get_raw_search(race, location, info_type)
    if results is None:
        results = web_search(user_id, query)

    prompt = f"""Race: {race}
Location: {location}
Requested info_type: {info_type}

Search results:
{results}

Original user query: {query}"""

    response = call_llm(
        system_prompt=RACE_INFO_SYSTEM, user_prompt=prompt, model="claude-haiku-4-5-20251001", cache_system=True
    )

    response = response.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    response = response[start:end]
    try:
        raw = json.loads(response)
        # info is a plain (non-discriminated) Union in RaceInfoPlan — model_validate_json would
        # silently match RaceRegistrationInfo first since all its fields are optional, dropping
        # race_day fields. Resolve the right submodel ourselves instead.
        info_model = INFO_MODELS[raw["info_type"]]
        parsed = RaceInfoPlan(
            info_type=raw["info_type"],
            race=raw["race"],
            location=raw["location"],
            info=info_model.model_validate(raw["info"]),
        )
    except Exception as e:
        print("Error parsing race info output:", e)
        raise

    info_dict = {
        "info_type": parsed.info_type,
        "race": parsed.race,
        "location": parsed.location,
        "info": parsed.info.model_dump(),
    }
    embedding = voyage_client.embed([query], model="voyage-3-lite").embeddings[0] if voyage_client else None
    set_cached(
        query, json.dumps(info_dict), parsed.race, parsed.location, parsed.info_type, embedding, raw_result=results
    )
    return info_dict
