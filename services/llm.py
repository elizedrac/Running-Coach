# Central call_llm() with retry + caching. All LLM traffic flows through here.
import os
import random
import time
import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 3

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def call_llm(system_prompt: str, user_prompt: str = None, model: str = DEFAULT_MODEL, max_tokens: int = 1024, cache_system: bool = False) -> str:
    system = (
        [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system_prompt
    )
    for attempt in range(MAX_RETRIES):
        try:
            message = client.messages.create(
                model=model,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=max_tokens,
            )
            return (message.content[0].text or "").strip()

        except anthropic.RateLimitError as e:
            _backoff(attempt, e)
        except anthropic.APIStatusError as e:
            if e.status_code < 500:
                raise
            _backoff(attempt, e)
        except anthropic.APIConnectionError as e:
            _backoff(attempt, e)

    raise RuntimeError(f"call_llm failed after {MAX_RETRIES} retries")

def stream_llm(system_prompt: str, user_prompt: str, model: str = DEFAULT_MODEL, max_tokens: int = 1024, cache_system: bool = False):
    """Yields text chunks as Claude generates them."""
    system = (
        [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system_prompt
    )
    with client.messages.stream(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=max_tokens,
    ) as stream:
        for text in stream.text_stream:
            yield text


def _backoff(attempt: int, error: Exception) -> None:
    if attempt == MAX_RETRIES - 1:
        raise error
    wait = (2 ** attempt) + random.uniform(0, 1)
    print(f"LLM error ({error}), retrying in {wait:.1f}s...")
    time.sleep(wait)
