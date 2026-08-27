# Central call_llm() with retry + caching. All LLM traffic flows through here.
import os
import random
import time

import anthropic
from dotenv import load_dotenv

from services.logging_config import get_logger

load_dotenv()

logger = get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 3

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=600.0)


def _log_usage(event: str, model: str, usage, started: float, **extra) -> None:
    """Token counts only. Prompt and response bodies carry health data and never get logged."""
    fields = {
        "model": model,
        "duration_ms": round((time.monotonic() - started) * 1000),
        **extra,
    }
    if usage is not None:
        fields.update(
            {
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None),
                "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", None),
            }
        )
    logger.info(event, extra=fields)


def call_llm(
    system_prompt: str,
    user_prompt: str = None,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    cache_system: bool = False,
) -> str:
    system = (
        [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system_prompt
    )
    for attempt in range(MAX_RETRIES):
        started = time.monotonic()
        try:
            message = client.messages.create(
                model=model,
                system=system,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=max_tokens,
            )
            _log_usage("llm_call", model, message.usage, started, stop_reason=message.stop_reason)
            return (message.content[0].text or "").strip()

        except anthropic.RateLimitError as e:
            _backoff(attempt, e, model)
        except anthropic.APIStatusError as e:
            if e.status_code < 500:
                logger.error("llm_client_error", extra={"model": model, "status": e.status_code}, exc_info=True)
                raise
            _backoff(attempt, e, model)
        except anthropic.APIConnectionError as e:
            _backoff(attempt, e, model)

    logger.error("llm_exhausted_retries", extra={"model": model, "retries": MAX_RETRIES})
    raise RuntimeError(f"call_llm failed after {MAX_RETRIES} retries")


def stream_llm(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 1024,
    cache_system: bool = False,
    extra_system: str = None,
):
    """Yields text chunks as Claude generates them."""
    system = (
        [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]
        if cache_system
        else system_prompt
    )
    if extra_system:
        if isinstance(system, str):
            system = [{"type": "text", "text": system}]
        system.append({"type": "text", "text": extra_system})
    started = time.monotonic()
    chunks = 0
    with client.messages.stream(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
        max_tokens=max_tokens,
    ) as stream:
        for text in stream.text_stream:
            chunks += 1
            yield text
        # Usage is only final once the stream drains. A caller that breaks early
        # (user hit Stop) skips this, which is why chunk counts are logged too.
        try:
            final = stream.get_final_message()
            _log_usage("llm_stream", model, final.usage, started, chunks=chunks, stop_reason=final.stop_reason)
        except Exception:
            logger.warning("llm_stream_usage_unavailable", extra={"model": model, "chunks": chunks})


def _backoff(attempt: int, error: Exception, model: str = DEFAULT_MODEL) -> None:
    if attempt == MAX_RETRIES - 1:
        raise error
    wait = (2**attempt) + random.uniform(0, 1)
    logger.warning(
        "llm_retry",
        extra={
            "model": model,
            "attempt": attempt + 1,
            "wait_s": round(wait, 1),
            "reason": type(error).__name__,
        },
    )
    time.sleep(wait)
