import hashlib
from models.planner import CourseDetailsPlan 
import json
from services.llm import call_llm
import voyageai
from pathlib import Path
from services.web_search import web_search
from dotenv import load_dotenv
import numpy as np
import os
import sys

_cached_chunks = None
_file_hash = None

THRESHOLD_SIMILARITY = 0.7

load_dotenv()

VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY")
voyage_client = voyageai.Client(api_key=VOYAGE_API_KEY)

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
    
def _embedd_chunks(chunks):
    unembedded = [chunk for chunk in chunks if "embedding" not in chunk]
    if not unembedded:
        return
    
    embeddings = voyage_client.embed([chunk["query"] for chunk in unembedded], model="voyage-3-lite").embeddings
    for chunk, embedding in zip(unembedded, embeddings):
        chunk["embedding"] = embedding
    CHUNKS_FILE.write_text(json.dumps({"courses": chunks}, indent=2))

def _compute_similarity(vec1, vec2):
    return sum(a * b for a, b in zip(vec1, vec2))

def find_relevant_chunks(query):
    embedded_query = voyage_client.embed([query], model="voyage-3-lite").embeddings[0]

    if CHUNKS_FILE.exists():
        chunks = _load_chunks()
        _embedd_chunks(chunks)
        embedded_chunks = [chunk['embedding'] for chunk in chunks if "embedding" in chunk]

        similarities = [_compute_similarity(embedded_query, chunk_embedding) for chunk_embedding in embedded_chunks]
        similarities = np.array(similarities)

        if len(similarities) > 0:
            best_idx = int(np.argmax(similarities))
            if "--debug" in sys.argv:
                print(f"[course_details] top match score: {similarities[best_idx]:.3f} | chunk query: {chunks[best_idx]['query']}", file=sys.stderr)
            if similarities[best_idx] > THRESHOLD_SIMILARITY:
                return {"query": chunks[best_idx]["query"], "details": chunks[best_idx]["details"]}
            
def _add_course_chunk(query: str, details: str) -> None:
    chunks = _load_chunks()
    embedded_query = voyage_client.embed([query], model="voyage-3-lite").embeddings[0]
    chunks.append({"query": query, "details": details, "embedding": embedded_query})
    CHUNKS_FILE.write_text(json.dumps({"courses": chunks}, indent=2))

def get_course_details(user_id: str, query: str):
    relevant = find_relevant_chunks(query)
    if relevant:
        if "--debug" in sys.argv:
            print("Found relevant course chunk with query:", relevant["query"])
        return relevant
    
    results = web_search(user_id, query)

    prompt = f"""Extract key details about this running race course and return as JSON with two fields:
- "query": a short semantic label summarising what the user asked (used for search)
- "details": a 3-5 sentence summary covering elevation, terrain/surface, notable sections, and race-day logistics

Course description:
{results}

Original user query: {query}

Return ONLY valid JSON, no extra text:
{{"query": "...", "details": "..."}}"""

    response = call_llm(system_prompt="You are a JSON-only assistant. Return valid JSON, nothing else.", user_prompt=prompt,
        model="claude-haiku-4-5-20251001").strip()
    
    response = response.strip()
    start = response.find("{")
    end = response.rfind("}") + 1
    response = response[start:end]
    try:
        response = CourseDetailsPlan.model_validate_json(response)
        _add_course_chunk(response.query, response.details)
        return {"query": response.query, "details": response.details}
   
    except Exception as e:
        print("Error parsing planner output:", e)
        print("Raw response was:", response)
        raise    


