# Anthropic web search wrapper. Caller (services/race_info.py) is responsible for caching the result.
import os

import anthropic
from dotenv import load_dotenv

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


WEB_SEARCH_SYSTEM = (
    "You are a research assistant gathering precise facts from web search. "
    "Transcribe specific numbers, dates, fees, and tables VERBATIM from the source pages — "
    "do not paraphrase or summarize away exact figures. If the query asks for a breakdown "
    "(e.g. by age group, gender, category), search until you find and include every value in "
    "that breakdown, not just a general description of the structure. If multiple searches are "
    "needed to find the specific data requested, keep searching before answering."
)


def web_search(user_id: str, query: str) -> str:
    """Run Anthropic's web search tool and return the result text."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        system=WEB_SEARCH_SYSTEM,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": query}],
    )

    text_parts = [block.text for block in response.content if hasattr(block, "text")]
    return "\n".join(text_parts).strip()
