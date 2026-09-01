import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
import voyageai
from dotenv import load_dotenv

from models.planner import CourseDetailsPlan
from services.llm import call_llm, extract_json
from services.logging_config import get_logger
from services.prompts import COURSE_DETAILS
from services.web_search import web_search

logger = get_logger(__name__)

_cached_chunks = None
_file_hash = None

THRESHOLD_SIMILARITY = 0.7

load_dotenv()

VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY) if VOYAGE_API_KEY else None

CHUNKS_FILE = Path(__file__).parent.parent.joinpath("knowledge/course_chunks.json")


def _load_chunks():
    global _cached_chunks, _file_hash
    if CHUNKS_FILE.exists():
        current_hash = hashlib.md5(CHUNKS_FILE.read_bytes()).hexdigest()
        if current_hash == _file_hash:
            return _cached_chunks
        _file_hash = current_hash
        data = json.loads(CHUNKS_FILE.read_text())
        _cached_chunks = data.get("courses", [])
        return _cached_chunks
    else:
        _cached_chunks = []
        _file_hash = None
        return _cached_chunks


def _embed_chunks(all_chunks, candidates):
    unembedded = [chunk for chunk in candidates if "embedding" not in chunk]
    if not unembedded:
        return

    embeddings = voyage_client.embed([chunk["query"] for chunk in unembedded], model="voyage-3-lite").embeddings
    for chunk, embedding in zip(unembedded, embeddings):
        chunk["embedding"] = embedding
    CHUNKS_FILE.write_text(json.dumps({"courses": all_chunks}, indent=2))


def _compute_similarity(vec1, vec2):
    return sum(a * b for a, b in zip(vec1, vec2))


def _word_overlap(a: str, b: str) -> float:
    wa = set(re.sub(r"[^a-z0-9 ]", "", a.lower()).split())
    wb = set(re.sub(r"[^a-z0-9 ]", "", b.lower()).split())
    union = wa | wb
    return len(wa & wb) / len(union) if union else 0.0


def find_relevant_chunks(location: str, race: str, query: str):
    chunks = _load_chunks()
    candidates = [
        c
        for c in chunks
        if c.get("location", "").lower() == location.lower() and c.get("race", "").lower() == race.lower()
    ]
    if not candidates:
        return None

    # Skip embedding when query closely matches a cached chunk query by word overlap
    for c in candidates:
        if _word_overlap(query, c.get("query", "")) >= 0.7:
            logger.debug("course_chunk_overlap_hit", extra={"chunk_query": c["query"]})
            return {"query": c["query"], "details": c["details"]}

    _embed_chunks(chunks, candidates)
    embedded_query = voyage_client.embed([query], model="voyage-3-lite").embeddings[0]
    similarities = np.array(
        [_compute_similarity(embedded_query, c["embedding"]) for c in candidates if "embedding" in c]
    )

    if len(similarities) > 0:
        best_idx = int(np.argmax(similarities))
        logger.debug(
            "course_chunk_similarity",
            extra={"score": round(float(similarities[best_idx]), 3), "chunk_query": candidates[best_idx]["query"]},
        )
        if similarities[best_idx] > THRESHOLD_SIMILARITY:
            return {"query": candidates[best_idx]["query"], "details": candidates[best_idx]["details"]}
    return None


def _add_course_chunk(location: str, race: str, query: str, details: str) -> None:
    chunks = _load_chunks()
    embedded_query = voyage_client.embed([query], model="voyage-3-lite").embeddings[0]
    chunks.append(
        {
            "location": location,
            "race": race,
            "query": query,
            "details": details,
            "embedding": embedded_query,
        }
    )
    CHUNKS_FILE.write_text(json.dumps({"courses": chunks}, indent=2))


def get_course_details(user_id: str, location: str, race: str, query: str):
    relevant = find_relevant_chunks(location, race, query)
    if relevant:
        logger.debug("course_chunk_hit", extra={"chunk_query": relevant["query"]})
        return relevant

    results = web_search(user_id, query)

    prompt = f"""
    Course description:
    {results}

    Original user query: {query}

    Return ONLY valid JSON, no extra text:
    {{"location": "...", "race": "...", "query": "...", "details": "..."}}"""

    response = call_llm(
        system_prompt=COURSE_DETAILS, user_prompt=prompt, model="claude-haiku-4-5-20251001", cache_system=True
    )

    response = response.strip()
    try:
        response = CourseDetailsPlan.model_validate(extract_json(response) or {})
        _add_course_chunk(response.location, response.race, response.query, response.details)
        return {"query": response.query, "details": response.details}
    except Exception:
        logger.error("course_details_parse_failed", extra={"race": race, "location": location}, exc_info=True)
        logger.debug("course_details_raw_response", extra={"raw": response})
        raise
